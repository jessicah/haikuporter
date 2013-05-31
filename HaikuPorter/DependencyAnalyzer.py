# -*- coding: utf-8 -*-
# copyright 2013 Ingo Weinhold

# -- Modules ------------------------------------------------------------------

from HaikuPorter.Utils import check_output

import glob
import os
import shutil
from subprocess import check_call, CalledProcessError

# -----------------------------------------------------------------------------

requiresDummyPackageInfo = r'''
name			_dummy_
version			1-1
architecture	any
summary			"dummy"
description		"dummy"
packager		"dummy <dummy@dummy.dummy>"
vendor			"Haiku Project"
licenses		"MIT"
copyrights		"none"
provides {
	_dummy_ = 1-1
}
requires {
	%s
}
'''

# -- PortNode class ------------------------------------------------------------

class PortNode(object):
	def __init__(self, portID, port):
		self.portID = portID
		self.port = port
		self.areDependenciesResolved = False
		self.packageNodes = set()
		self.requires = set()
		self.buildRequires = set()
		self.buildPrerequires = set()
		self.indegree = 0

	def getName(self):
		return self.portID

	def getDependencies(self):
		return self.buildRequires | self.buildPrerequires

	def isPort(self):
		return True

	def doesBuildDependOnSelf(self):
		return not (self.buildRequires.isdisjoint(self.packageNodes)
			and self.buildPrerequires.isdisjoint(self.packageNodes))

	def addBuildRequires(self, elements):
		self.buildRequires |= elements

	def addBuildPrerequires(self, elements):
		self.buildPrerequires |= elements

# -- PackageNode class ---------------------------------------------------------

class PackageNode(object):
	def __init__(self, portNode, packageID):
		self.portNode = portNode
		self.packageID = packageID
		self.requires = set()
		self.indegree = 0

	def getName(self):
		return self.packageID

	def isPort(self):
		return False

	def getDependencies(self):
		dependencies = self.requires
		dependencies.add(self.portNode)
		return dependencies

	def isSystemPackage(self):
		return not self.portNode

	def addRequires(self, elements):
		self.requires |= elements

# -- DependencyAnalyzer class --------------------------------------------------

class DependencyAnalyzer(object):
	def __init__(self, repository):
		self.repository = repository

		# Remove and re-create the no-requires repository directory. It
		# simplifies resolving the  immediate requires for all ports.
		print 'Preparing no-requires repository ...'

		self.noRequiresRepositoryPath = self.repository.path + '.no-requires'

		if os.path.exists(self.noRequiresRepositoryPath):
			shutil.rmtree(self.noRequiresRepositoryPath)
		os.mkdir(self.noRequiresRepositoryPath)

		packageInfos = glob.glob(self.repository.path + '/*.PackageInfo')
		packageIDs = []
		for packageInfo in packageInfos:
			packageInfoFileName = os.path.basename(packageInfo)
			packageIDs.append(
				packageInfoFileName[:packageInfoFileName.rindex('.')])
			destinationPath = (self.noRequiresRepositoryPath + '/'
				+ packageInfoFileName)
			self._stripRequiresFromPackageInfo(packageInfo, destinationPath)

		# Remove and re-create the system no-requires repository directory. It
		# contains the package info for system packages without requires.
		print 'Preparing no-requires system repository ...'

		self.noRequiresSystemRepositoryPath = (self.repository.path
			+ '.no-requires-system')

		if os.path.exists(self.noRequiresSystemRepositoryPath):
			shutil.rmtree(self.noRequiresSystemRepositoryPath)
		os.mkdir(self.noRequiresSystemRepositoryPath)

		# we temporarily need an empty directory to check the package infos
		self.emptyDirectory = self.noRequiresSystemRepositoryPath + '/empty'
		os.mkdir(self.emptyDirectory)

		for directory in ['/boot/system/packages', '/boot/common/packages']:
			for package in os.listdir(directory):
				if not package.endswith('.hpkg'):
					continue

				# extract the package info from the package file
				fileName = package[:-5] + '.PackageInfo'
				destinationPath = (self.noRequiresSystemRepositoryPath + '/'
					+ fileName)
				sourcePath = destinationPath + '.tmp'
				check_call(['package', 'extract', '-i', sourcePath,
					directory + '/' + package, '.PackageInfo'])

				# strip the requires section from the package info
				self._stripRequiresFromPackageInfo(sourcePath, destinationPath)
				os.remove(sourcePath)

				if not self._isPackageInfoValid(destinationPath):
					print ('Warning: Ignoring invalid package info from %s'
						% package)
					os.remove(destinationPath)

		os.rmdir(self.emptyDirectory)

		# Iterate through the packages and resolve dependencies. We build a
		# dependency graph with two different node types: port nodes and package
		# nodes. A port is something we want to build, a package is a what we
		# depend on. A package automatically depends on the port it belongs to.
		# Furthermore it depends on the packages its requires specify. Build
		# requires and build prerequires are dependencies for a port.
		print 'Resolving dependencies ...'

		allPorts = self.repository.getAllPorts()
		self.portNodes = {}
		self.packageNodes = {}
		self.allRequires = {}
		for packageID in packageIDs:
			# get the port ID for the package
			portID = packageID
			if portID not in allPorts:
				portID = self.repository.getPortIdForPackageId(portID)

			portNode = self._getPortNode(portID)
			if portNode.areDependenciesResolved:
				continue

			for package in portNode.port.packages:
				packageID = package.name + '-' + portNode.port.version
				packageNode = self._getPackageNode(packageID)

				recipeKeys = package.getRecipeKeys()
				packageNode.addRequires(
					self._resolveRequiresList(recipeKeys['REQUIRES']))
				portNode.addBuildRequires(
					self._resolveRequiresList(recipeKeys['BUILD_REQUIRES']))
				portNode.addBuildPrerequires(
					self._resolveRequiresList(recipeKeys['BUILD_PREREQUIRES']))

			portNode.areDependenciesResolved = True

		# print the needed system packages
		print 'Required system packages:'

		nonSystemPortNodes = set()

		for packageNode in self.packageNodes.itervalues():
			if packageNode.isSystemPackage():
				print '  %s' % packageNode.getName()
			else:
				nonSystemPortNodes.add(packageNode.portNode)

		# print the self-depending ports
		print 'Self depending ports:'

		nodes = set()

		for portNode in nonSystemPortNodes:
			if portNode.doesBuildDependOnSelf():
				print '  %s' % portNode.portID
			else:
				nodes.add(portNode)
				nodes |= portNode.packageNodes

		# compute the in-degrees of the nodes
		for node in nodes:
			for dependency in node.getDependencies():
				if dependency in nodes:
					dependency.indegree += 1

		indegreeZeroStack = []
		for node in nodes:
			if node.indegree == 0:
				indegreeZeroStack.append(node)

		# remove the acyclic part of the graph
		while indegreeZeroStack:
			node = indegreeZeroStack.pop()
			nodes.remove(node)
			for dependency in node.getDependencies():
				if dependency in nodes:
					dependency.indegree -= 1
					if dependency.indegree == 0:
						indegreeZeroStack.append(dependency)

		# print the remaining cycle(s)
		print 'Ports depending cyclically on each other:'

		for node in nodes:
			if node.isPort():
				print '  %s' % node.getName()

		# clean up
		shutil.rmtree(self.noRequiresRepositoryPath)
		shutil.rmtree(self.noRequiresSystemRepositoryPath)

	def _resolveRequiresList(self, requiresList):
		dependencies = set()
		for requires in requiresList:
			# filter comments
			index = requires.find('#')
			if index >= 0:
				requires = requires[:index]
			requires = requires.strip()
			if not requires:
				continue

			# resolve the requires
			if requires in self.allRequires:
				resolved = self.allRequires[requires]
			else:
				resolved = self._resolveRequires(requires)
				self.allRequires[requires] = resolved
			if resolved:
				dependencies.add(resolved)
			else:
				print 'Warning: Ignoring unresolvable requires "%s"' % requires
		return dependencies

	def _resolveRequires(self, requires):
		# write the dummy package info with the requires to be resolved
		dummyPath = (self.noRequiresRepositoryPath
			+ '/_dummy_-1-1-any.PackageInfo')
		with open(dummyPath, 'w') as dummyFile:
			dummyFile.write(requiresDummyPackageInfo % requires)

		# let pkgman resolve the dependency
		isSystemPackage = False
		args = [ '/bin/pkgman', 'resolve-dependencies', dummyPath,
			self.noRequiresRepositoryPath ]
		try:
			with open(os.devnull, "w") as devnull:
				output = check_output(args, stderr=devnull)
		except CalledProcessError:
			try:
				args[-1] = self.noRequiresSystemRepositoryPath
				with open(os.devnull, "w") as devnull:
					output = check_output(args, stderr=devnull)
					isSystemPackage = True
			except CalledProcessError:
				return None

		lines = output.splitlines()
		if not lines:
			return None
		if len(lines) > 1:
			print 'Warning: Got multiple results for requires "%s"' % requires

		packageID = os.path.basename(lines[0])
		suffix = '.PackageInfo'
		if packageID.endswith(suffix):
			packageID = packageID[:-len(suffix)]
		packageIDComponents = packageID.split('-')
		if len(packageIDComponents) > 1:
			packageID = packageIDComponents[0] + '-' + packageIDComponents[1]
		else:
			packageID = packageIDComponents[0]

		return self._getPackageNode(packageID, isSystemPackage)

	def _isPackageInfoValid(self, packageInfoPath):
		args = [ '/bin/pkgman', 'resolve-dependencies', packageInfoPath,
			self.emptyDirectory ]
		try:
			with open(os.devnull, "w") as devnull:
				check_call(args, stderr=devnull)
				return True
		except CalledProcessError:
			return False

	def _getPortNode(self, portID):
		if portID in self.portNodes:
			return self.portNodes[portID]

		# get the port and create the port node
		port = self.repository.getAllPorts()[portID]
		portNode = PortNode(portID, port)
		self.portNodes[portID] = portNode

		# also create nodes for all of the port's packages
		portNode.port.parseRecipeFile(False)
		for package in port.packages:
			packageID = package.name + '-' + port.version
			packageNode = PackageNode(portNode, packageID)
			self.packageNodes[packageID] = packageNode
			portNode.packageNodes.add(packageNode)

		return portNode

	def _getPackageNode(self, packageID, isSystemPackage = False):
		if packageID in self.packageNodes:
			return self.packageNodes[packageID]

		if isSystemPackage:
			packageNode = PackageNode(None, packageID)
			self.packageNodes[packageID] = packageNode
			return packageNode

		# get the port -- that will also create nodes for all of the port's
		# packages
		portID = packageID
		if portID not in self.repository.getAllPorts():
			portID = self.repository.getPortIdForPackageId(portID)
		self._getPortNode(portID)

		if not packageID in self.packageNodes:
			sysExit('package "%s" doesn\'t seem to exist' % packageID)
		return self.packageNodes[packageID]

	def _stripRequiresFromPackageInfo(self, sourcePath, destinationPath):
		with open(sourcePath, 'r') as sourceFile:
			with open(destinationPath, 'w') as destinationFile:
				isInRequires = False
				for line in sourceFile:
					if isInRequires:
						if line == '}\n':
							isInRequires = False
					else:
						if line == 'requires {\n':
							isInRequires = True
						else:
							destinationFile.write(line)
