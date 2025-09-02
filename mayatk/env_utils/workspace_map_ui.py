# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'workspace_map.ui'
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
from PySide6.QtWidgets import (QApplication, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QPushButton, QSizePolicy,
    QSpacerItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget)

class Ui_workspace_map(object):
    def setupUi(self, workspace_map):
        if not workspace_map.objectName():
            workspace_map.setObjectName(u"workspace_map")
        workspace_map.resize(600, 500)
        self.verticalLayout = QVBoxLayout(workspace_map)
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.groupBox = QGroupBox(workspace_map)
        self.groupBox.setObjectName(u"groupBox")
        self.horizontalLayout = QHBoxLayout(self.groupBox)
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.label = QLabel(self.groupBox)
        self.label.setObjectName(u"label")

        self.horizontalLayout.addWidget(self.label)

        self.txt000 = QLineEdit(self.groupBox)
        self.txt000.setObjectName(u"txt000")

        self.horizontalLayout.addWidget(self.txt000)

        self.b002 = QPushButton(self.groupBox)
        self.b002.setObjectName(u"b002")

        self.horizontalLayout.addWidget(self.b002)


        self.verticalLayout.addWidget(self.groupBox)

        self.groupBox_2 = QGroupBox(workspace_map)
        self.groupBox_2.setObjectName(u"groupBox_2")
        self.horizontalLayout_2 = QHBoxLayout(self.groupBox_2)
        self.horizontalLayout_2.setObjectName(u"horizontalLayout_2")
        self.label_2 = QLabel(self.groupBox_2)
        self.label_2.setObjectName(u"label_2")

        self.horizontalLayout_2.addWidget(self.label_2)

        self.txt001 = QLineEdit(self.groupBox_2)
        self.txt001.setObjectName(u"txt001")

        self.horizontalLayout_2.addWidget(self.txt001)


        self.verticalLayout.addWidget(self.groupBox_2)

        self.tree000 = QTreeWidget(workspace_map)
        self.tree000.setObjectName(u"tree000")

        self.verticalLayout.addWidget(self.tree000)

        self.horizontalLayout_3 = QHBoxLayout()
        self.horizontalLayout_3.setObjectName(u"horizontalLayout_3")
        self.horizontalSpacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.horizontalLayout_3.addItem(self.horizontalSpacer)

        self.b000 = QPushButton(workspace_map)
        self.b000.setObjectName(u"b000")

        self.horizontalLayout_3.addWidget(self.b000)

        self.b001 = QPushButton(workspace_map)
        self.b001.setObjectName(u"b001")

        self.horizontalLayout_3.addWidget(self.b001)


        self.verticalLayout.addLayout(self.horizontalLayout_3)


        self.retranslateUi(workspace_map)

        QMetaObject.connectSlotsByName(workspace_map)
    # setupUi

    def retranslateUi(self, workspace_map):
        workspace_map.setWindowTitle(QCoreApplication.translate("workspace_map", u"Workspace Map", None))
        self.groupBox.setTitle(QCoreApplication.translate("workspace_map", u"Directory Settings", None))
        self.label.setText(QCoreApplication.translate("workspace_map", u"Root Directory:", None))
#if QT_CONFIG(tooltip)
        self.txt000.setToolTip(QCoreApplication.translate("workspace_map", u"Root directory to search for workspaces", None))
#endif // QT_CONFIG(tooltip)
        self.b002.setText(QCoreApplication.translate("workspace_map", u"Refresh", None))
#if QT_CONFIG(tooltip)
        self.b002.setToolTip(QCoreApplication.translate("workspace_map", u"Refresh workspace list", None))
#endif // QT_CONFIG(tooltip)
        self.groupBox_2.setTitle(QCoreApplication.translate("workspace_map", u"Filter", None))
        self.label_2.setText(QCoreApplication.translate("workspace_map", u"Filter:", None))
#if QT_CONFIG(tooltip)
        self.txt001.setToolTip(QCoreApplication.translate("workspace_map", u"Filter workspace names", None))
#endif // QT_CONFIG(tooltip)
        ___qtreewidgetitem = self.tree000.headerItem()
        ___qtreewidgetitem.setText(2, QCoreApplication.translate("workspace_map", u"Size", None));
        ___qtreewidgetitem.setText(1, QCoreApplication.translate("workspace_map", u"Scenes", None));
        ___qtreewidgetitem.setText(0, QCoreApplication.translate("workspace_map", u"Workspace", None));
#if QT_CONFIG(tooltip)
        self.tree000.setToolTip(QCoreApplication.translate("workspace_map", u"Available workspaces organized by directory", None))
#endif // QT_CONFIG(tooltip)
        self.b000.setText(QCoreApplication.translate("workspace_map", u"Browse", None))
#if QT_CONFIG(tooltip)
        self.b000.setToolTip(QCoreApplication.translate("workspace_map", u"Browse for root directory", None))
#endif // QT_CONFIG(tooltip)
        self.b001.setText(QCoreApplication.translate("workspace_map", u"Set to Workspace", None))
#if QT_CONFIG(tooltip)
        self.b001.setToolTip(QCoreApplication.translate("workspace_map", u"Set to current Maya workspace", None))
#endif // QT_CONFIG(tooltip)
    # retranslateUi

