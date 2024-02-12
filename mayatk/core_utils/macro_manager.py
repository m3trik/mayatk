# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk


class MacroManager(ptk.HelpMixin):
    """Assign macro functions to hotkeys.

    Example:
        class Macros(MacroManager):
            '''A class that inherits from `MacroManager` and holds the actual macro functions.
            '''
            @staticmethod
            def m_back_face_culling():
                    '''Toggle Back-Face Culling.
                    '''
                    sel = pm.ls(selection=True)
                    if sel:
                            currentPanel = getPanel(withFocus=True)
                            state = pm.polyOptions(sel, query=True, wireBackCulling=True)[0]

                            if not state:
                                    pm.polyOptions(sel, gl=True, wireBackCulling=True)
                                    Macros.setWireframeOnShadedOption(currentPanel, 0)
                                    pm.inViewMessage(status_message="Back-Face Culling is now <hl>OFF</hl>.>", pos='topCenter', fade=True)
                            else:
                                    pm.polyOptions(sel, gl=True, backCulling=True)
                                    Macros.setWireframeOnShadedOption(currentPanel, 1)
                                    pm.inViewMessage(status_message="Back-Face Culling is now <hl>ON</hl>.", pos='topCenter', fade=True)
                    else:
                            print(" Warning: Nothing selected. ")

        #call the `set_macros` function to set a macro for functions you defined in `Macros`.
        mtk.Macros.set_macros(
            "m_back_face_culling,     key=1, cat=Display",
        )
    """

    @classmethod
    def set_macros(cls, *args):
        """Extends `set_macro` to accept a list of strings representing positional and keyword arguments.

        Parameters:
            *args (str): A variable number of strings, each containing the arguments for a single macro. Each string
                    should be in the format "<macro name>, <positional arg1>, <positional arg2>, ..., <keyword arg1>=<value1>,
                    <keyword arg2>=<value2>, ..."
        Example:
            set_macros('m_back_face_culling, key=1, cat=Display', 'm_smooth_preview, key=2, cat=Display') #Calls `set_macro` with the parsed arguments for each macro in `args`.
        """
        for string in args:
            cls.call_with_input(cls.set_macro, string)

    @staticmethod
    def call_with_input(func, input_string):
        """Parses an input string into positional and keyword arguments, and
        calls the given function with those arguments.

        Parameters:
            func (callable): The function to call.
            input_string (str): The input string containing the arguments.

        Returns:
            The result of calling `func` with the parsed arguments.
        """
        args, kwargs = [], {}
        for i in input_string.split(","):
            try:
                key, value = i.split("=")
                kwargs[key.strip()] = value.strip()
            except ValueError:
                args.append(i.strip())

        return func(*args, **kwargs)

    @classmethod
    def set_macro(
        cls, name, key=None, cat=None, ann=None, default=False, delete_existing=True
    ):
        """Sets a default runtime command with a keyboard shortcut.

        Parameters:
            name (str): The command name you provide must be unique. (alphanumeric characters, or underscores)
            cat (str): catagory - Category for the command.
            ann (str): annotation - Description of the command.
            key (str): keyShortcut - Specify what key is being set.
                                    key modifier values are set by adding a '+' between chars. ie. 'sht+z'.
                                    modifiers:
                                            alt, ctl, sht
                                    additional valid keywords are:
                                            Up, Down, Right, Left,
                                            Home, End, Page_Up, Page_Down, Insert
                                            Return, Space
                                            F1 to F12
                                            Tab (Will only work when modifiers are specified)
                                            Delete, Backspace (Will only work when modifiers are specified)
            default (bool): Indicate that this run time command is a default command. Default run time commands will not be saved to preferences.
            delete_existing = Delete any existing (non-default) runtime commands of the given name.
        """
        command = f"if 'm_slots' not in globals(): from {cls.__module__} import {cls.__name__}; global m_slots; m_slots = {cls.__name__}();\nm_slots.{name}();"

        if not ann:  # if no ann is given, try using the method's docstring.
            method = getattr(cls, name)
            ann = method.__doc__.split("\n")[0]  # use only the first line.

        if pm.runTimeCommand(name, exists=True):
            if pm.runTimeCommand(name, query=True, default=True):
                return  # can not delete default runtime commands.
            elif (
                delete_existing
            ):  # delete any existing (non-default) runtime commands of that name.
                pm.runTimeCommand(name, edit=True, delete=True)

        try:  # set runTimeCommand
            pm.runTimeCommand(
                name,
                annotation=ann,
                category=cat,
                command=command,
                default=default,
            )
        except RuntimeError as error:
            print("# Error: {}: {} #".format(__file__, error))
            return error

        # set command
        nameCommand = pm.nameCommand(
            "{0}Command".format(name),
            annotation=ann,
            command=name,
        )

        # set hotkey
        # modifiers
        ctl = False
        alt = False
        sht = False
        for char in key.split("+"):
            if char == "ctl":
                ctl = True
            elif char == "alt":
                alt = True
            elif char == "sht":
                sht = True
            else:
                key = char

        # print(name, char, ctl, alt, sht)
        pm.hotkey(
            keyShortcut=key, name=nameCommand, ctl=ctl, alt=alt, sht=sht
        )  # set only the key press.


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------

"""
#create wrapper
mel.createMelWrapper(method)

#set command
pm.nameCommand('name', annotation='', command=<>)
pm.hotkey(key='1', altModifier=True, name='name')


#clear keyboard shortcut
pm.hotkey(keyShortcut=key, name='', releaseName='', ctl=ctl, alt=alt, sht=sht) #unset the key press name and releaseName.


#query runTimeCommand
if pm.runTimeCommand('name', exists=True):


#delete runTimeCommand
pm.runTimeCommand('name', edit=True, delete=True)


#set runTimeCommand
pm.runTimeCommand(
            'name',
            annotation=string,
            category=string,
            categoryArray,
            command=script,
            commandArray,
            commandLanguage=string,
            default=boolean,
            defaultCommandArray,
            delete,
            exists,
            hotkeyCtx=string,
            image=string,
            keywords=string,
            annotation=string,
            longAnnotation=string,
            numberOfCommands,
            numberOfDefaultCommands,
            numberOfUserCommands,
            plugin=string,
            save,
            showInHotkeyEditor=boolean,
            tags=string,
            userCommandArray,
)

-annotation(-ann) string createqueryedit 
        Description of the command.

-category(-cat) string createqueryedit  
        Category for the command.

-categoryArray(-caa) query          
        Return all the run time command categories.

-command(-c) script createqueryedit     
        Command to be executed when runTimeCommand is invoked.

-commandArray(-ca) query                
        Returns an string array containing the names of all the run time commands.

-commandLanguage(-cl) string createqueryedit
        In edit or create mode, this flag allows the caller to choose a scripting language for a command passed to the "-command" flag. If this flag is not specified, then the callback will be assumed to be in the language from which the runTimeCommand command was called. In query mode, the language for this runTimeCommand is returned. The possible values are "mel" or "python".

-default(-d) boolean createquery        
        Indicate that this run time command is a default command. Default run time commands will not be saved to preferences.

-defaultCommandArray(-dca) query                
        Returns an string array containing the names of all the default run time commands.

-delete(-del) edit              
        Delete the specified user run time command.

-exists(-ex) create                 
        Returns true|false depending upon whether the specified object exists. Other flags are ignored.

-hotkeyCtx(-hc) string createqueryedit  
        hotkey Context for the command.

-image(-i) string createqueryedit   
        Image filename for the command.

-keywords(-k) string createqueryedit        
        Keywords for the command. Used for searching for commands in Type To Find. When multiple keywords, use ; as a separator. (Example: "keyword1;keyword2")

-annotation(-annotation) string createqueryedit     
        Label for the command.

-longAnnotation(-la) string createqueryedit 
        Extensive, multi-line description of the command. This will show up in Type To Finds more info page in addition to the annotation.

-numberOfCommands(-nc) query            
        Return the number of run time commands.

-numberOfDefaultCommands(-ndc) query            
        Return the number of default run time commands.

-numberOfUserCommands(-nuc) query           
        Return the number of user run time commands.

-plugin(-p) string createqueryedit          
        Name of the plugin this command requires to be loaded. This flag wraps the script provided into a safety check and automatically loads the plugin referenced on execution if it hasn't been loaded. If the plugin fails to load, the command won't be executed.

-save(-s) edit                          
        Save all the user run time commands.

-showInHotkeyEditor(-she) boolean createqueryedit       
        Indicate that this run time command should be shown in the Hotkey Editor. Default value is true.

-tags(-t) string createqueryedit    
        Tags for the command. Used for grouping commands in Type To Find. When more than one tag, use ; as a separator. (Example: "tag1;tag2")

-userCommandArray(-uca) query           
        Returns an string array containing the names of all the user run time commands.
"""
