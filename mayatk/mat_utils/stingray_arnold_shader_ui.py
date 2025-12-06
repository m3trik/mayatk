# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'stingray_arnold_shader.ui'
##
## Created by: Qt User Interface Compiler version 6.9.1
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor,
    QFont, QFontDatabase, QGradient, QIcon,
    QImage, QKeySequence, QLinearGradient, QPainter,
    QPalette, QPixmap, QRadialGradient, QTransform)
from PySide6.QtWidgets import (QAbstractScrollArea, QApplication, QCheckBox, QComboBox,
    QGridLayout, QGroupBox, QHBoxLayout, QLayout,
    QLineEdit, QMainWindow, QProgressBar, QPushButton,
    QSizePolicy, QTabWidget, QTextEdit, QVBoxLayout,
    QWidget)

from uitk.widgets.collapsableGroup.CollapsableGroup import CollapsableGroup
from uitk.widgets.comboBox.ComboBox import ComboBox
from uitk.widgets.header.Header import Header

class Ui_QtUi(object):
    def setupUi(self, QtUi):
        if not QtUi.objectName():
            QtUi.setObjectName(u"QtUi")
        QtUi.setEnabled(True)
        QtUi.resize(500, 331)
        QtUi.setTabShape(QTabWidget.Triangular)
        QtUi.setDockNestingEnabled(True)
        QtUi.setDockOptions(QMainWindow.AllowNestedDocks|QMainWindow.AllowTabbedDocks|QMainWindow.AnimatedDocks|QMainWindow.ForceTabbedDocks)
        self.central_widget = QWidget(QtUi)
        self.central_widget.setObjectName(u"central_widget")
        self.central_widget.setMinimumSize(QSize(500, 0))
        self.verticalLayout = QVBoxLayout(self.central_widget)
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.main_layout = QVBoxLayout()
        self.main_layout.setSpacing(6)
        self.main_layout.setObjectName(u"main_layout")
        self.main_layout.setSizeConstraint(QLayout.SetFixedSize)
        self.header = Header(self.central_widget)
        self.header.setObjectName(u"header")
        self.header.setMinimumSize(QSize(0, 22))
        self.header.setMaximumSize(QSize(999, 22))
        font = QFont()
        font.setBold(True)
        self.header.setFont(font)

        self.main_layout.addWidget(self.header)

        self.main_group = QGroupBox(self.central_widget)
        self.main_group.setObjectName(u"main_group")
        sizePolicy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.main_group.sizePolicy().hasHeightForWidth())
        self.main_group.setSizePolicy(sizePolicy)
        self.main_group.setMinimumSize(QSize(0, 180))
        self.gridLayout = QGridLayout(self.main_group)
        self.gridLayout.setObjectName(u"gridLayout")
        self.gridLayout.setHorizontalSpacing(1)
        self.gridLayout.setVerticalSpacing(0)
        self.gridLayout.setContentsMargins(0, 0, 0, 0)
        self.create_network_group = QGroupBox(self.main_group)
        self.create_network_group.setObjectName(u"create_network_group")
        self.create_network_group.setMinimumSize(QSize(0, 87))
        self.verticalLayout_2 = QVBoxLayout(self.create_network_group)
        self.verticalLayout_2.setSpacing(1)
        self.verticalLayout_2.setObjectName(u"verticalLayout_2")
        self.verticalLayout_2.setContentsMargins(0, 0, 0, 0)
        self.b001 = QPushButton(self.create_network_group)
        self.b001.setObjectName(u"b001")
        self.b001.setEnabled(True)
        sizePolicy.setHeightForWidth(self.b001.sizePolicy().hasHeightForWidth())
        self.b001.setSizePolicy(sizePolicy)
        self.b001.setMinimumSize(QSize(200, 30))
        self.b001.setMaximumSize(QSize(16777215, 30))

        self.verticalLayout_2.addWidget(self.b001)

        self.b000 = QPushButton(self.create_network_group)
        self.b000.setObjectName(u"b000")
        self.b000.setEnabled(False)
        sizePolicy.setHeightForWidth(self.b000.sizePolicy().hasHeightForWidth())
        self.b000.setSizePolicy(sizePolicy)
        self.b000.setMinimumSize(QSize(200, 30))
        self.b000.setMaximumSize(QSize(16777215, 30))

        self.verticalLayout_2.addWidget(self.b000)

        self.progressBar = QProgressBar(self.create_network_group)
        self.progressBar.setObjectName(u"progressBar")
        self.progressBar.setMinimumSize(QSize(0, 0))
        self.progressBar.setMaximumSize(QSize(16777215, 4))
        self.progressBar.setValue(0)
        self.progressBar.setTextVisible(False)

        self.verticalLayout_2.addWidget(self.progressBar)


        self.gridLayout.addWidget(self.create_network_group, 8, 0, 1, 3)

        self.graph_options = QHBoxLayout()
        self.graph_options.setObjectName(u"graph_options")
        self.cmb004 = ComboBox(self.main_group)
        self.cmb004.addItem("")
        self.cmb004.addItem("")
        self.cmb004.setObjectName(u"cmb004")
        self.cmb004.setMinimumSize(QSize(0, 20))
        self.cmb004.setMaximumSize(QSize(999, 20))
        self.cmb004.setEditable(False)
        self.cmb004.setMaxVisibleItems(30)
        self.cmb004.setMaxCount(20)
        self.cmb004.setInsertPolicy(QComboBox.InsertAtTop)
        self.cmb004.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.cmb004.setFrame(False)

        self.graph_options.addWidget(self.cmb004)


        self.gridLayout.addLayout(self.graph_options, 2, 0, 1, 1)

        self.extension_group_2 = QGroupBox(self.main_group)
        self.extension_group_2.setObjectName(u"extension_group_2")
        self.extension_group_2.setMinimumSize(QSize(0, 40))
        self.extension_group_2.setMaximumSize(QSize(16777215, 40))
        self.horizontalLayout_11 = QHBoxLayout(self.extension_group_2)
        self.horizontalLayout_11.setSpacing(1)
        self.horizontalLayout_11.setObjectName(u"horizontalLayout_11")
        self.horizontalLayout_11.setContentsMargins(1, 1, 0, 1)
        self.cmb003 = ComboBox(self.extension_group_2)
        self.cmb003.addItem("")
        self.cmb003.addItem("")
        self.cmb003.addItem("")
        self.cmb003.addItem("")
        self.cmb003.addItem("")
        self.cmb003.addItem("")
        self.cmb003.setObjectName(u"cmb003")
        self.cmb003.setMinimumSize(QSize(0, 20))
        self.cmb003.setMaximumSize(QSize(999, 20))
        self.cmb003.setEditable(False)
        self.cmb003.setMaxVisibleItems(30)
        self.cmb003.setMaxCount(20)
        self.cmb003.setInsertPolicy(QComboBox.InsertAtTop)
        self.cmb003.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.cmb003.setFrame(False)

        self.horizontalLayout_11.addWidget(self.cmb003)


        self.gridLayout.addWidget(self.extension_group_2, 0, 1, 1, 1)

        self.material_name_group = QGroupBox(self.main_group)
        self.material_name_group.setObjectName(u"material_name_group")
        self.material_name_group.setMinimumSize(QSize(300, 40))
        self.material_name_group.setMaximumSize(QSize(16777215, 50))
        self.horizontalLayout_5 = QHBoxLayout(self.material_name_group)
        self.horizontalLayout_5.setSpacing(1)
        self.horizontalLayout_5.setObjectName(u"horizontalLayout_5")
        self.horizontalLayout_5.setContentsMargins(0, 1, 1, 1)
        self.txt000 = QLineEdit(self.material_name_group)
        self.txt000.setObjectName(u"txt000")
        self.txt000.setMinimumSize(QSize(0, 20))
        self.txt000.setMaximumSize(QSize(999, 16777215))

        self.horizontalLayout_5.addWidget(self.txt000)


        self.gridLayout.addWidget(self.material_name_group, 0, 0, 1, 1)

        self.output_template_group = QGroupBox(self.main_group)
        self.output_template_group.setObjectName(u"output_template_group")
        self.output_template_group.setMinimumSize(QSize(0, 40))
        self.output_template_group.setMaximumSize(QSize(16777215, 40))
        self.horizontalLayout_9 = QHBoxLayout(self.output_template_group)
        self.horizontalLayout_9.setSpacing(1)
        self.horizontalLayout_9.setObjectName(u"horizontalLayout_9")
        self.horizontalLayout_9.setContentsMargins(1, 1, 0, 1)
        self.cmb002 = ComboBox(self.output_template_group)
        self.cmb002.addItem("")
        self.cmb002.addItem("")
        self.cmb002.addItem("")
        self.cmb002.addItem("")
        self.cmb002.addItem("")
        self.cmb002.addItem("")
        self.cmb002.addItem("")
        self.cmb002.setObjectName(u"cmb002")
        self.cmb002.setMinimumSize(QSize(0, 20))
        self.cmb002.setMaximumSize(QSize(999, 20))
        self.cmb002.setEditable(False)
        self.cmb002.setMaxVisibleItems(30)
        self.cmb002.setMaxCount(20)
        self.cmb002.setInsertPolicy(QComboBox.InsertAtTop)
        self.cmb002.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.cmb002.setFrame(False)

        self.horizontalLayout_9.addWidget(self.cmb002)


        self.gridLayout.addWidget(self.output_template_group, 1, 0, 1, 1)

        self.normal_output_group = QGroupBox(self.main_group)
        self.normal_output_group.setObjectName(u"normal_output_group")
        self.normal_output_group.setMinimumSize(QSize(0, 40))
        self.normal_output_group.setMaximumSize(QSize(16777215, 40))
        self.horizontalLayout_7 = QHBoxLayout(self.normal_output_group)
        self.horizontalLayout_7.setSpacing(1)
        self.horizontalLayout_7.setObjectName(u"horizontalLayout_7")
        self.horizontalLayout_7.setContentsMargins(1, 1, 0, 1)
        self.cmb001 = ComboBox(self.normal_output_group)
        self.cmb001.addItem("")
        self.cmb001.addItem("")
        self.cmb001.setObjectName(u"cmb001")
        self.cmb001.setMinimumSize(QSize(0, 20))
        self.cmb001.setMaximumSize(QSize(999, 20))
        self.cmb001.setEditable(False)
        self.cmb001.setMaxVisibleItems(30)
        self.cmb001.setMaxCount(20)
        self.cmb001.setInsertPolicy(QComboBox.InsertAtTop)
        self.cmb001.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.cmb001.setFrame(False)

        self.horizontalLayout_7.addWidget(self.cmb001)


        self.gridLayout.addWidget(self.normal_output_group, 1, 1, 1, 1)

        self.chk000 = QCheckBox(self.main_group)
        self.chk000.setObjectName(u"chk000")

        self.gridLayout.addWidget(self.chk000, 2, 1, 1, 1)


        self.main_layout.addWidget(self.main_group)

        self.output_group = CollapsableGroup(self.central_widget)
        self.output_group.setObjectName(u"output_group")
        self.output_group.setAlignment(Qt.AlignCenter)
        self.verticalLayout_3 = QVBoxLayout(self.output_group)
        self.verticalLayout_3.setObjectName(u"verticalLayout_3")
        self.verticalLayout_3.setContentsMargins(0, 0, 0, 0)
        self.txt001 = QTextEdit(self.output_group)
        self.txt001.setObjectName(u"txt001")
        self.txt001.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.txt001.setSizeAdjustPolicy(QAbstractScrollArea.AdjustIgnored)

        self.verticalLayout_3.addWidget(self.txt001)


        self.main_layout.addWidget(self.output_group)


        self.verticalLayout.addLayout(self.main_layout)

        QtUi.setCentralWidget(self.central_widget)

        self.retranslateUi(QtUi)

        QMetaObject.connectSlotsByName(QtUi)
    # setupUi

    def retranslateUi(self, QtUi):
        QtUi.setWindowTitle(QCoreApplication.translate("QtUi", u"Create Shader Network", None))
        self.header.setText(QCoreApplication.translate("QtUi", u"CREATE STINGRAY SHADER", None))
        self.main_group.setTitle("")
        self.create_network_group.setTitle("")
        self.b001.setText(QCoreApplication.translate("QtUi", u"Get Texture Maps", None))
        self.b000.setText(QCoreApplication.translate("QtUi", u"Create Network", None))
        self.cmb004.setItemText(0, QCoreApplication.translate("QtUi", u"Stingray PBS", None))
        self.cmb004.setItemText(1, QCoreApplication.translate("QtUi", u"Standard Surface", None))

#if QT_CONFIG(tooltip)
        self.cmb004.setToolTip(QCoreApplication.translate("QtUi", u"Select the shader type.", None))
#endif // QT_CONFIG(tooltip)
        self.extension_group_2.setTitle(QCoreApplication.translate("QtUi", u"Ext", None))
        self.cmb003.setItemText(0, QCoreApplication.translate("QtUi", u"PNG", None))
        self.cmb003.setItemText(1, QCoreApplication.translate("QtUi", u"JPG", None))
        self.cmb003.setItemText(2, QCoreApplication.translate("QtUi", u"BMP", None))
        self.cmb003.setItemText(3, QCoreApplication.translate("QtUi", u"TGA", None))
        self.cmb003.setItemText(4, QCoreApplication.translate("QtUi", u"TIFF", None))
        self.cmb003.setItemText(5, QCoreApplication.translate("QtUi", u"GIF", None))

#if QT_CONFIG(tooltip)
        self.cmb003.setToolTip(QCoreApplication.translate("QtUi", u"File type for the output maps.", None))
#endif // QT_CONFIG(tooltip)
        self.material_name_group.setTitle(QCoreApplication.translate("QtUi", u"Material Name", None))
#if QT_CONFIG(tooltip)
        self.txt000.setToolTip(QCoreApplication.translate("QtUi", u"<html><head/><body><p>Name the material.  </p><p>If None is given, the material name will be derived from the given texture names.</p></body></html>", None))
#endif // QT_CONFIG(tooltip)
        self.txt000.setPlaceholderText(QCoreApplication.translate("QtUi", u"<Material Name>", None))
        self.output_template_group.setTitle(QCoreApplication.translate("QtUi", u"Output Template", None))
        self.cmb002.setItemText(0, QCoreApplication.translate("QtUi", u"PBR Metallic/Roughness (Separate Maps)", None))
        self.cmb002.setItemText(1, QCoreApplication.translate("QtUi", u"Unity URP Lit (Packed: Albedo+Alpha, Metallic+Smoothness)", None))
        self.cmb002.setItemText(2, QCoreApplication.translate("QtUi", u"Unity HDRP Lit (Mask Map: Metallic+AO+Detail+Smoothness)", None))
        self.cmb002.setItemText(3, QCoreApplication.translate("QtUi", u"Unreal Engine (Packed: BaseColor+Alpha, ORM)", None))
        self.cmb002.setItemText(4, QCoreApplication.translate("QtUi", u"glTF 2.0 (Separate: BaseColor, Metallic, Roughness)", None))
        self.cmb002.setItemText(5, QCoreApplication.translate("QtUi", u"Godot (Separate: Albedo, Metallic, Roughness)", None))
        self.cmb002.setItemText(6, QCoreApplication.translate("QtUi", u"PBR Specular/Glossiness Workflow", None))

#if QT_CONFIG(tooltip)
        self.cmb002.setToolTip("")
#endif // QT_CONFIG(tooltip)
        self.normal_output_group.setTitle(QCoreApplication.translate("QtUi", u"Normal Map", None))
        self.cmb001.setItemText(0, QCoreApplication.translate("QtUi", u"OpenGL", None))
        self.cmb001.setItemText(1, QCoreApplication.translate("QtUi", u"DirectX", None))

#if QT_CONFIG(tooltip)
        self.cmb001.setToolTip(QCoreApplication.translate("QtUi", u"Select the normal map output type.", None))
#endif // QT_CONFIG(tooltip)
#if QT_CONFIG(tooltip)
        self.chk000.setToolTip(QCoreApplication.translate("QtUi", u"<html><head/><body><p>Sets up a basic Arnold shader network for use with the StingrayPBS node.<br/><br/>This method loads the MtoA plugin if not already loaded, creates an aiStandardSurface</p><p>shader, an aiMultiply utility node, and a bump2d node for normal mapping. It connects</p><p>these nodes together and to the StingrayPBS node's shading engine to integrate Arnold</p><p>rendering with Stingray materials.</p></body></html>", None))
#endif // QT_CONFIG(tooltip)
        self.chk000.setText(QCoreApplication.translate("QtUi", u"AiBridge", None))
        self.output_group.setTitle(QCoreApplication.translate("QtUi", u"\u2022 \u2022 \u2022", None))
    # retranslateUi

