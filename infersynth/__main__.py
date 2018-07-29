




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