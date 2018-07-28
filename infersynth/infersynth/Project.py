import configparser as ConfigParser

class project:

    def __init__(self):
        from time import gmtime, strftime
        self.update = strftime("%Y-%m-%d-%H%M%S", gmtime())
        self.version = 1
        self.last_client = "cynth"
        self.lib1 = ""
        self.libpath = ""
        self.database = ""
        self.project_name = ""
        self.config_path = ""

    def open_project(self, config_path):
        if config_path is None:
            pass
        else:
            # if we have a config path so try to open the project
            fn = config_path[0]
            config = ConfigParser.RawConfigParser()
            try:
                config.read(fn)
                self.update = config.get('cynth', 'update')
                self.version = config.get('cynth', 'version')
                self.project_name = config.get('cynth', 'project_name')
                self.config_path = config_path  # set the current project file
            except:
                print("exception couldn't read %s!" % config_path)

    def save_project(self):
        # if we have a config path then we can assume the project is up to date and can save the project
        config = ConfigParser.ConfigParser()
        from time import gmtime, strftime
        dt = strftime("%Y-%m-%d-%H%M%S", gmtime())
        try:
            config['cynth'] = {'update': str(dt) ,'version': self.version, 'project_name': self.project_name}
            with open(self.config_path[0], 'w') as project_file:
                config.write(project_file)
            print("Project Saved")
        except:
            print("File Failed to Open")


    def new_project(self, config_path):
        if config_path is None:
            pass
        else:
            # if we have a config path then this is a new project
            self.fn = config_path[0]
            self.config_path = config_path
            names = config_path[0].lstrip('/').split('/')
            self.project_name = names[-1]
            config = ConfigParser.ConfigParser()
            from time import gmtime, strftime
            dt = strftime("%Y-%m-%d-%H%M%S", gmtime())
            try:

                print(self.fn)
                config['cynth'] = {'update': str(dt) ,'version': self.version, 'project_name': self.project_name}
                with open(self.fn, 'w') as project_file:
                    config.write(project_file)
                print("Project Created")
            except:
                print("File Failed to Open")

    def loadConfigFileInt(self, filename, section, option):
        """ Returns the setting requested from the config file

        :param filename: configuration file to load
        :param section: section within the configuration file
        :param option: option name within the specified section
        :return: setting
        """
        config = ConfigParser.RawConfigParser()
        config.read(filename)
        try:
            setting = config.getint(section, option)
            return setting
        except:
            print("exception on %s!" % option)
            return None