# !/usr/bin/python
# coding=utf-8
import os, sys
import unittest
import inspect

import pymel.core as pm

from mayatk import Core


# sfr = pm.melGlobals['cmdScrollFieldReporter']
# pm.cmdScrollFieldReporter(sfr, edit=1, clear=1)


class Main(unittest.TestCase):
	'''
	'''
	@staticmethod
	def replace_mem_address(obj):
		'''Replace memory addresses in a string representation of an object with a fixed format of '0x00000000000'.

		Parameters:
			obj (object): The input object. The function first converts this object to a string using the `str` function.

		Return:
			(str) The string representation of the object with all memory addresses replaced.

		Example:
			>>> replace_mem_address("<class 'str'> <PySide2.QtWidgets.QWidget(0x1ebe2677e80, name='MayaWindow') at 0x000001EBE6D48500>")
			"<class 'str'> <PySide2.QtWidgets.QWidget(0x00000000000, name='MayaWindow') at 0x00000000000>"
		'''
		import re
		return re.sub(r'0x[a-fA-F\d]+', '0x00000000000', str(obj))

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



class Core_test(Main, Core):
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
	#test imports:
	import mayatk as mtk
	from mayatk import Cmpt
	from mayatk import getComponents

	#Tear down the any previous test by creating a new scene:
	pm.mel.file(new=True, force=True)

	#assemble the test scene:
	if not pm.objExists('cyl'):
		cyl = pm.polyCylinder(radius=5, height=10, subdivisionsX=12, subdivisionsY=1, subdivisionsZ=1, name='cyl')

	def test_undo(self):
		'''
		'''
		self.perform_test({
			"self.replace_mem_address(self.undo())": '<function Core.undo.<locals>.wrapper at 0x00000000000>',
		})

	def test_getMainWindow(self):
		'''
		'''
		self.perform_test({
			"self.replace_mem_address(self.getMainWindow())": "<PySide2.QtWidgets.QWidget(0x00000000000, name=\"MayaWindow\") at 0x00000000000>" or None,
		})

	def test_mfnMeshGenerator(self):
		'''
		'''
		self.perform_test({
			"str(next(self.mfnMeshGenerator('cyl'))).split(';')[0]": '<maya.OpenMaya.MFnMesh',
		})

	def test_getArrayType(self):
		'''
		'''
		self.perform_test({
			"self.getArrayType(100)": 'int',
			"self.getArrayType('cylShape.vtx[:]')": 'str',
			"self.getArrayType(pm.ls('cylShape.vtx[:]'))": 'obj',
		})

	def test_convertArrayType(self):
		'''
		'''
		self.perform_test({
			"self.convertArrayType('cyl.vtx[:2]', 'str')": ['cylShape.vtx[0:2]'],
			"self.convertArrayType('cyl.vtx[:2]', 'str', flatten=True)": ['cylShape.vtx[0]', 'cylShape.vtx[1]', 'cylShape.vtx[2]'],
			"str(self.convertArrayType('cyl.vtx[:2]', 'obj'))": "[MeshVertex('cylShape.vtx[0:2]')]",
			"str(self.convertArrayType('cyl.vtx[:2]', 'obj', flatten=True))": "[MeshVertex('cylShape.vtx[0]'), MeshVertex('cylShape.vtx[1]'), MeshVertex('cylShape.vtx[2]')]",
			"self.convertArrayType('cyl.vtx[:2]', 'int')": [0, 2],
			"self.convertArrayType('cyl.vtx[:2]', 'int', flatten=True)": [0, 1, 2],
		})

	def test_getParameterValuesMEL(self):
		'''
		'''
		self.perform_test({
			# "self.getParameterValuesMEL()": None,
		})

	def test_setParameterValuesMEL(self):
		'''
		'''
		self.perform_test({
			# "self.setParameterValuesMEL()": None,
		})

	def test_getSelectedChannels(self):
		'''
		'''
		self.perform_test({
			# "self.getSelectedChannels()": None,
		})

	def test_getPanel(self):
		'''
		'''
		self.perform_test({
			# "self.getPanel()": None,
		})

	def test_mainProgressBar(self):
		'''
		'''
		self.perform_test({
			# "self.mainProgressBar()": None,
		})

	def test_viewportMessage(self):
		'''
		'''
		self.perform_test({
			# "self.viewportMessage()": None,
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