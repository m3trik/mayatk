# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'hud.ui'
##
## Created by: Qt User Interface Compiler version 5.15.2
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide2.QtCore import *
from PySide2.QtGui import *
from PySide2.QtWidgets import *

from widgets.textedit import TextEdit


class Ui_QtUi(object):
    def setupUi(self, QtUi):
        if not QtUi.objectName():
            QtUi.setObjectName(u"QtUi")
        QtUi.setEnabled(True)
        QtUi.resize(600, 300)
        sizePolicy = QSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(QtUi.sizePolicy().hasHeightForWidth())
        QtUi.setSizePolicy(sizePolicy)
        QtUi.setMinimumSize(QSize(600, 300))
        QtUi.setCursor(QCursor(Qt.ArrowCursor))
        QtUi.setWindowOpacity(1.000000000000000)
        QtUi.setStyleSheet(u"QMainWindow {\n"
"			background-color: rgba(127,127,127,2); \n"
"}")
        QtUi.setTabShape(QTabWidget.Triangular)
        QtUi.setDockNestingEnabled(True)
        QtUi.setDockOptions(QMainWindow.AllowNestedDocks|QMainWindow.AllowTabbedDocks|QMainWindow.AnimatedDocks|QMainWindow.ForceTabbedDocks)
        self.hud_widget = QWidget(QtUi)
        self.hud_widget.setObjectName(u"hud_widget")
        sizePolicy.setHeightForWidth(self.hud_widget.sizePolicy().hasHeightForWidth())
        self.hud_widget.setSizePolicy(sizePolicy)
        self.gridLayout = QGridLayout(self.hud_widget)
        self.gridLayout.setObjectName(u"gridLayout")
        self.hud_text = TextEdit(self.hud_widget)
        self.hud_text.setObjectName(u"hud_text")
        sizePolicy1 = QSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.MinimumExpanding)
        sizePolicy1.setHorizontalStretch(0)
        sizePolicy1.setVerticalStretch(0)
        sizePolicy1.setHeightForWidth(self.hud_text.sizePolicy().hasHeightForWidth())
        self.hud_text.setSizePolicy(sizePolicy1)
        self.hud_text.setMinimumSize(QSize(300, 0))
        self.hud_text.setMaximumSize(QSize(800, 800))
        self.hud_text.viewport().setProperty("cursor", QCursor(Qt.ArrowCursor))
        self.hud_text.setMouseTracking(False)
        self.hud_text.setFocusPolicy(Qt.NoFocus)
        self.hud_text.setContextMenuPolicy(Qt.NoContextMenu)
        self.hud_text.setFrameShape(QFrame.NoFrame)
        self.hud_text.setFrameShadow(QFrame.Plain)
        self.hud_text.setLineWidth(0)
        self.hud_text.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.hud_text.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.hud_text.setSizeAdjustPolicy(QAbstractScrollArea.AdjustToContents)
        self.hud_text.setUndoRedoEnabled(False)
        self.hud_text.setReadOnly(True)
        self.hud_text.setAcceptRichText(True)
        self.hud_text.setTextInteractionFlags(Qt.NoTextInteraction)

        self.gridLayout.addWidget(self.hud_text, 1, 1, 1, 1)

        self.verticalSpacer_2 = QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding)

        self.gridLayout.addItem(self.verticalSpacer_2, 2, 1, 1, 1)

        self.verticalSpacer = QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding)

        self.gridLayout.addItem(self.verticalSpacer, 0, 1, 1, 1)

        self.horizontalSpacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.gridLayout.addItem(self.horizontalSpacer, 1, 0, 1, 1)

        self.horizontalSpacer_2 = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.gridLayout.addItem(self.horizontalSpacer_2, 1, 2, 1, 1)

        QtUi.setCentralWidget(self.hud_widget)

        self.retranslateUi(QtUi)

        QMetaObject.connectSlotsByName(QtUi)
    # setupUi

    def retranslateUi(self, QtUi):
        pass
    # retranslateUi

