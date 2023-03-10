# !/usr/bin/python
# coding=utf-8
import os, sys
import unittest
import inspect

import pymel.core as pm

from mayatk.Node import Node


# sfr = pm.melGlobals['cmdScrollFieldReporter']
# pm.cmdScrollFieldReporter(sfr, edit=1, clear=1)


class Main(unittest.TestCase):
	'''
	'''
	def perform_test(self, case):
		'''
		'''
		for expression, expected_result in case.items():
			m = str(expression).split('(')[0] #ie. 'self.setCase' from "self.setCase('xxx', 'upper')"

			try:
				path = os.path.abspath(inspect.getfile(eval(m)))
			except TypeError as error:
				path = ''

			result = eval(expression)
			self.assertEqual(
				result, 
				expected_result, 
				f"\n\n# Error: {path}\n#\tCall: {expression.replace('self.', '', 1)}\n#\tExpected {type(expected_result)}: {expected_result}\n#\tReturned {type(result)}: {result}"
			)



class Node_test(Main, Node):
	'''
	set object mode:
		pm.selectMode(object=1)

	set component mode:
		pm.selectMode(component=1)

	set component mode type:
		pm.selectType(allObjects=1)
		pm.selectType(mc=1)
		pm.selectType(vertex=1)
		pm.selectType(edge=1)
		pm.selectType(facet=1)
		pm.selectType(polymeshUV=1)
		pm.selectType(meshUVShell=1)
	'''
	#Tear down the any previous test by creating a new scene:
	pm.mel.file(new=True, force=True)

	#assemble the test scene:
	if not pm.objExists('cyl'):
		cyl = pm.polyCylinder(radius=5, height=10, subdivisionsX=12, subdivisionsY=1, subdivisionsZ=1, name='cyl')

	def test_getType(self):
		'''
		'''
		self.perform_test({
			"self.getType('cyl')": 'transform',
			"self.getType('cylShape')": 'mesh',
			"self.getType('cylShape.vtx[0]')": 'vtx',
			"self.getType('cylShape.e[0]')": 'e',
			"self.getType('cylShape.f[0]')": 'f',
		})

	def test_getTransformNode(self):
		'''
		'''
		self.perform_test({
			"self.getTransformNode('cyl')": 'cyl',
			"self.getTransformNode('cylShape')": 'cyl',
		})

	def test_getShapeNode(self):
		'''
		'''
		self.perform_test({
			"self.getShapeNode('cyl')": 'cylShape',
			"self.getShapeNode('cylShape')": 'cylShape',
		})

	def test_getHistoryNode(self):
		'''
		'''
		self.perform_test({
			"self.getHistoryNode('cyl')": 'polyCylinder1',
			"self.getHistoryNode('cylShape')": 'polyCylinder1',
		})

	def test_isLocator(self):
		'''
		'''
		if not pm.objExists('loc'):
			loc = pm.spaceLocator(name='loc')

		self.perform_test({
			"self.isLocator('cyl')": False,
			"self.isLocator('loc')": True,
		})

	def test_isGroup(self):
		'''
		'''
		self.perform_test({
			"self.isGroup('cyl')": False,
			"self.isGroup('cylShape')": False,
			"self.isGroup('cylShape.vtx[0]')": False,
		})

	def test_getGroups(self):
		'''
		'''
		self.perform_test({
			"self.getGroups()": [],
		})

	def test_getParent(self):
		'''
		'''
		self.perform_test({
			"self.getParent('cyl')": None,
		})

	def test_getChildren(self):
		'''
		'''
		self.perform_test({
			"self.getChildren('cyl')": [],
		})

	def test_getAttributesMEL(self):
		'''
		'''
		self.perform_test({
			# "self.getAttributesMEL()": None,
		})

	def test_setAttributesMEL(self):
		'''
		'''
		self.perform_test({
			# "self.setAttributesMEL()": None,
		})

	def test_connectAttributes(self):
		'''
		'''
		self.perform_test({
			# "self.connectAttributes()": None,
		})

	def test_createRenderNode(self):
		'''
		'''
		self.perform_test({
			# "self.createRenderNode()": None,
		})

	def test_getIncomingNodeByType(self):
		'''
		'''
		self.perform_test({
			# "self.getIncomingNodeByType()": None,
		})

	def test_getOutgoingNodeByType(self):
		'''
		'''
		self.perform_test({
			# "self.getOutgoingNodeByType()": None,
		})

	def test_connectMultiAttr(self):
		'''
		'''
		self.perform_test({
			# "self.connectMultiAttr()": None,
		})

	def test_nodeExists(self):
		'''
		'''
		self.perform_test({
			# "self.nodeExists()": None,
		})

# -----------------------------------------------------------------------------









# -----------------------------------------------------------------------------

if __name__=='__main__':

	unittest.main(exit=False)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------

# """

# def test_(self):
# 	'''
# 	'''
# 	self.perform_test({
# 		"self.()": None,
# 	})

# def test_(self):
# 	'''
# 	'''
# 	self.perform_test({
# 		# "self.": '',
# 	})

# def test_(self):
# 	'''
# 	'''
# 	self.perform_test({
# 		# "self.": '',
# 	})

# def test_(self):
# 	'''
# 	'''
# 	self.perform_test({
# 		# "self.": '',
# 	})
# """

# Deprecated ---------------------


# ------------------------------------------------------------------------------------
# this is the missing stuff when running python.exe compared with mayapy.exe

# mayaver = 2022
# pythonver = 37

# mayapath = '%ProgramFiles%/Autodesk/Maya{}'.format(mayaver)

# os.environ['MAYA_LOCATION'] = mayapath
# os.environ['PYTHONHOME'] = mayapath+'/Python{}'.format(mayaver, pythonver)
# os.environ['PATH'] = mayapath+'/bin;'.format(mayaver) + os.environ['PATH']

# from pythontk import File
# for d in [
# 	'{}/bin'.format(mayapath), 
# 	'{}/bin3'.format(mayapath), 
# 	'{}/Python{}'.format(mayapath, pythonver)
# 	]:
# 	for dd in File.getDirContents(d, 'dirpaths', excDirs='Python27',  recursive=True):
# 		print (dd)
# 		sys.path.append(dd)

# import maya.standalone
# maya.standalone.initialize(name='python')