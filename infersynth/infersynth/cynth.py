__author__ = 'Ross VonFange'


from PyQt5.uic import loadUiType
from PyQt5.QtCore import *
from PyQt5.QtGui import *
# ~~~~~~~~~~~~~~~~~~~~~~~~~
from PyQt5.QtWidgets import QApplication
from PyQt5.QtWidgets import QFileDialog

from infersynth import Project
import sqlite3


Ui_MainWindow, QMainWindow = loadUiType('cynthView.ui')
Ui_CreateCircuitDialog, QDialog = loadUiType('CreateCircuitDialog.ui')

class CreateCircuitDialog(QDialog, Ui_CreateCircuitDialog):
    def __init__(self):
        QDialog.__init__(self)
        # Set up the user interface from Designer.
        self.setupUi(self)

        self.button_save_configuration.clicked.connect(self.save_config)
        self.fn = "Controller.ams"
        self.plainTextEdit_configuration

        # Load the Text Edit area with the Controller.ams contents
        try:
            with open(self.fn, 'r') as f:
                text = f.read()
                self.plainTextEdit_configuration.setPlainText(text)
                f.close()
                print("File Loaded")

                try:
                    from time import gmtime, strftime
                    dt = strftime("%Y-%m-%d-%H%M%S", gmtime())
                    with open(str(dt) + "_" + self.fn, 'w') as bkup:
                        bkup.write(text)
                        bkup.close()
                        print("Backup Successful.")
                except:
                    print("Backing Up Configuration Failed!")
        except:
            print("File Failed to Open")

    def save_config(self):
        try:
            with open(self.fn, 'w') as f:
                f.write(str(self.plainTextEdit_configuration.toPlainText()))
                f.close()
                print("File Saved")
        except:
            print("File Failed to Open")


class Main(QMainWindow, Ui_MainWindow):

    def __init__(self, ):
        super(Main, self).__init__()
        self.setupUi(self)
        self.Open_Project_action.triggered.connect(self.open_project)
        self.Save_Project_action.triggered.connect(self.save_project)
        self.New_Project_action.triggered.connect(self.new_project)
        self.current_project = Project.project()

        conn = sqlite3.connect('example.db')
        self.testqtv()
        self.Catalog_treeView.doubleClicked.connect(self.treeCircuit_doubleClicked)




    def open_project(self):
        # Load project file
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        fn = QFileDialog.getOpenFileName(self, "Open Project","","Cynth Files (*.cpj);;All Files (*)", options=options)
        self.current_project.open_project(fn)
        print("Opened Project: ", self.current_project.project_name)

    def save_project(self):
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        self.Save_Project_action.triggered.connect(self.save_project)
        fn = QFileDialog.getSaveFileName(self, "Save Project","","Cynth Files (*.cpj);;All Files (*)", options=options)
        self.current_project.save_project(fn)

    def new_project(self):
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        fn = QFileDialog.getSaveFileName(self, "New Project","","Cynth Files (*.cpj);;All Files (*)", options=options)
        if fn:
            self.current_project.new_project(fn)
            print(fn)

    def testqtv(self):
        # init widgets
        model = QStandardItemModel()
        model.setHorizontalHeaderLabels(['col1', 'col2', 'col3'])
        self.Catalog_treeView.setModel(model)
        self.Catalog_treeView.setUniformRowHeights(True)
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        # populate data
        i=1
        parent1 = QStandardItem('Processors')
        for j in range(3):
            child1 = QStandardItem('STM32 {}'.format(i * 3 + j))
            child2 = QStandardItem('F103')
            child3 = QStandardItem('Description')
            parent1.appendRow([child1, child2, child3])
        model.appendRow(parent1)
        i = 2
        parent1 = QStandardItem('Peripherals')
        for j in range(3):
            child1 = QStandardItem('INEMO {}'.format(i * 3 + j))
            child2 = QStandardItem('LSM9DS1')
            child3 = QStandardItem('INU')
            parent1.appendRow([child1, child2, child3])
        model.appendRow(parent1)
        i = 3
        parent1 = QStandardItem('IO')
        for j in range(3):
            child1 = QStandardItem('Child {}'.format(i * 3 + j))
            child2 = QStandardItem('row: {}, col: {}'.format(i, j + 1))
            child3 = QStandardItem('row: {}, col: {}'.format(i, j + 2))
            parent1.appendRow([child1])
        model.appendRow(parent1)



        # span container columns
        self.Catalog_treeView.setFirstColumnSpanned(i, self.Catalog_treeView.rootIndex(), True)
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        # expand third container
        index = model.indexFromItem(parent1)
        self.Catalog_treeView.expand(index)
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        # select last row
        selmod = self.Catalog_treeView.selectionModel()
        index2 = model.indexFromItem(child3)
        selmod.select(index2, QItemSelectionModel.Select | QItemSelectionModel.Rows)
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    def treeCircuit_doubleClicked(self,index):
        item = self.Catalog_treeView.selectedIndexes()[0]
        print(item.model().itemFromIndex(index).text())


if __name__ == '__main__':
    import sys
    app = QApplication(sys.argv)
    main = Main()
    #main.addmpl()

    # show as modal window for debugging
    main.show()
    # show as full screen for regular use
    #main.showFullScreen()

    sys.exit(app.exec_())