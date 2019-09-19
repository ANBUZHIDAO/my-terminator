#!/usr/bin/env python2
#-*-coding: UTF-8 -*-

# by LiangYong
# GPL v2 only
"""ssh_connect.py - Terminator Plugin to manage SSH Connections """

import os
import sys
from gi.repository import GLib, GObject, Gtk, Vte
import terminatorlib.plugin as plugin
from terminatorlib.config import Config
from terminatorlib.translation import _
from terminatorlib.util import get_config_dir, err, dbg, gerr, shell_lookup

import terminatorlib.util
from terminatorlib.factory import Factory
from terminatorlib.notebook import TabLabel
import time
import keyring

(CC_COL_IP, CC_COL_USER, CC_COL_PORT, CC_COL_TIME) = range(0,4)
nowTime = lambda:int(round(time.time() * 1000))
SSH_CMD = 'sshpass -e ssh -p {port} -o StrictHostKeyChecking=no -o ConnectTimeout=8 -o LogLevel=Error {user}@{ip}'
SFTP_CMD = 'sshpass -e sftp -P {port} -o StrictHostKeyChecking=no -o ConnectTimeout=8 -o LogLevel=Error {user}@{ip}'
SYSTEM_NAME = 'terminator_ssh'
DEBUG_ENABLE = False         # 是否打印DEBUG日志

def log_debug(msg):
    if DEBUG_ENABLE:
        print("[DEBUG]: %s (%s:%d)" % (msg, __file__[__file__.rfind('/')+1:], sys._getframe(1).f_lineno))

def save_keyring_passwd(ssh_key, passwd):
    # 密码保存到keyring密钥环
    keyring.set_password(SYSTEM_NAME, ssh_key, passwd)

def delete_keyring_passwd(ssh_key):
    # 删除keyring密钥环里的密码
    try:
      keyring.delete_password(SYSTEM_NAME, ssh_key)
    except :
      log_debug("PasswordDeleteError, maybe No such password!")
    else:
      log_debug("delete_keyring_passwd success")

def get_keyring_passwd(ssh_key):
    # 获取keyring密钥环里的密码
    passwd = keyring.get_password(SYSTEM_NAME, ssh_key)
    if passwd is None:
      log_debug("passwd not found")
      return ''
    else:
      return passwd

# Every plugin you want Terminator to load *must* be listed in 'AVAILABLE'
AVAILABLE = ['SSHConnect']

class SSHConnect(plugin.MenuItem):
    """Add SSH Command to the terminal menu"""
    capabilities = ['terminal_menu']
    cmd_list = []

    def __init__( self):
      self.cmd_list = []
      self.cur_input = ''
      config = Config()
      sections = config.plugin_get_config(self.__class__.__name__)
      if not isinstance(sections, dict):
          return
      for part in sections:
        s = sections[part]
        if not (s.has_key("ip") and s.has_key("user")):
          print "SSH Configuration: Ignoring section %s" % s
          continue
        ip = s["ip"]
        user = s["user"]
        port = s["port"]
        last_time = s["last_time"]

        self.cmd_list.append({ 'ip' : ip, 'user' : user, 
                               'port' : port,'last_time':last_time 
                            })

    def new_connect_tab0(self, _widget, data, connect_commnd=SSH_CMD):
        ip = data['ip']
        user = data['user']
        port = data['port']
        connect_commnd = connect_commnd.format(user=user,ip=ip,port=port)
        ssh_key = user + '@' + ip
        passwd = get_keyring_passwd(ssh_key)

        log_debug(connect_commnd)
        self.new_connect_tab(connect_commnd, passwd)

    def new_connect_tab(self, command, passwd):

        log_debug("new_connect_tab")
        top_window = self.terminal.get_toplevel()
        if top_window.get_property('term_zoomed') == True:
            err("You can't create a tab while a terminal is maximised/zoomed")
            return

        profile = self.terminal.get_profile()
        log_debug(profile)

        maker = Factory()
        if not top_window.is_child_notebook():
            dbg('Making a new Notebook')
            notebook = maker.make('Notebook', window=top_window)
        top_window.show()
        top_window.present()

        """Add a new tab, optionally supplying a child widget"""
        dbg('making a new tab')
        notebook = top_window.get_child()
        log_debug(notebook)

        widget = maker.make('Terminal')
        if profile and notebook.config['always_split_with_profile']:
            widget.force_set_profile(None, profile)

        args = []
        shell = None

        if widget.terminator.doing_layout == True:
            dbg('still laying out, refusing to spawn a child')
            return

        widget.vte.grab_focus()
        #设置为家目录
        widget.set_cwd(os.environ['HOME'])

        shell = shell_lookup()
        args.insert(0, shell)
        # 不加 || export -n SSHPASS; shell，如果连接失败将立即退出，看不到任何信息
        # 加了之后ssh登录失败将回退到 shell，可以看到失败信息，
        # ssh登录成功则执行 exit退出ssh后将关闭窗口
        args += ['-c', command + " || (export -n SSHPASS; " + shell + ")"]
        # 加; export -n SSHPASS; shell 后执行exit退出ssh后将回到shell  
        # args += ['-c', command + "; export -n SSHPASS; " + shell]    

        if shell is None:
            widget.vte.feed(_('Unable to find a shell'))
            return(-1)

        try:
            os.putenv('WINDOWID', '%s' % widget.vte.get_parent_window().xid)
        except AttributeError:
            pass

        envv = []
        envv.append('SSHPASS=%s' % passwd)
        envv.append('TERM=%s' % widget.config['term'])
        envv.append('COLORTERM=%s' % widget.config['colorterm'])
        envv.append('PWD=%s' % widget.cwd)
        envv.append('TERMINATOR_UUID=%s' % widget.uuid.urn)
        if widget.terminator.dbus_name:
            envv.append('TERMINATOR_DBUS_NAME=%s' % widget.terminator.dbus_name)
        if widget.terminator.dbus_path:
            envv.append('TERMINATOR_DBUS_PATH=%s' % widget.terminator.dbus_path)

        log_debug('Forking shell: "%s" with args: %s' % (shell, args))
        args.insert(0, shell)
        log_debug(args)
        result,  widget.pid = widget.vte.spawn_sync(Vte.PtyFlags.DEFAULT,
                                       widget.cwd,
                                       args,
                                       envv,
                                       GLib.SpawnFlags.FILE_AND_ARGV_ZERO | GLib.SpawnFlags.DO_NOT_REAP_CHILD ,
                                       None,
                                       None,
                                       None)
        widget.command = shell
        widget.titlebar.update()

        #将新创建的terminal创建一个新tab
        notebook.newtab(debugtab=False, widget=widget)
        if widget.pid == -1:
            widget.vte.feed(_('Unable to start shell:') + shell)
            return(-1)

    def callback(self, menuitems, menu, terminal):
        """Add our menu items to the menu"""
        submenus = {}
        item = Gtk.MenuItem.new_with_mnemonic(_('_SSH Connect'))
        menuitems.append(item)

        submenu = Gtk.Menu()
        item.set_submenu(submenu)

        menuitem = Gtk.MenuItem.new_with_mnemonic(_('_SSH Config'))
        menuitem.connect("activate", self.configure)
        submenu.append(menuitem)

        menuitem = Gtk.SeparatorMenuItem()
        submenu.append(menuitem)

        theme = Gtk.IconTheme.get_default()
        self.cmd_list.sort(key = lambda x:x['last_time'],reverse=True)
        count = 0
        for ssh_conf in self.cmd_list :
          ssh_key = ssh_conf['user'] + '@' + ssh_conf['ip']
          menuitem = Gtk.MenuItem(ssh_key)
          menuitem.connect("activate", self.new_connect_tab0, ssh_conf)
          submenu.append(menuitem)

          count = count + 1
          if count >= 10:
            break

        self.terminal = terminal
    
    def _save_config(self):
      log_debug("_save_config")
      config = Config()
      config.plugin_del_config(self.__class__.__name__)
      i = 0
      for ssh_conf in self.cmd_list :
        ip = ssh_conf['ip']
        user = ssh_conf['user']
        port = ssh_conf['port']
        last_time = ssh_conf['last_time']

        item = { 'ip': ip, 'user': user, 'port': port, 'last_time': last_time}
        ssh_key = user+"@"+ip
        config.plugin_set(self.__class__.__name__, ssh_key, item)

        i = i + 1
      config.save()

    def on_ssh_connect(self, widget, data):
      self.on_click_connect(widget,data,SSH_CMD)

    def on_sftp_connect(self, widget, data):
      self.on_click_connect(widget,data,SFTP_CMD)

    def on_click_connect(self, widget, data, connect_commnd):
      treeview = data['treeview']
      selection = treeview.get_selection()
      (store_filter, filter_iter) = selection.get_selected()
      
      store = store_filter.get_model()
      iter = store_filter.convert_iter_to_child_iter(filter_iter)
      if iter:
        ssh_conf = {}
        ssh_conf['ip'] = store.get_value(iter, CC_COL_IP)
        ssh_conf['user'] = store.get_value(iter, CC_COL_USER)
        ssh_conf['port'] = store.get_value(iter, CC_COL_PORT)
        store.set_value(iter, CC_COL_TIME, nowTime())

        self.new_connect_tab0(None, ssh_conf, connect_commnd)
        self.dbox.response(Gtk.ResponseType.ACCEPT)

    def configure(self, widget, data = None):
      ui = {}
      dbox = Gtk.Dialog(
                      _("SSH Connections Configuration"),
                      None,
                      Gtk.DialogFlags.MODAL,
                      (
                        _("_OK"), Gtk.ResponseType.ACCEPT
                      )
                    )
      dbox.set_transient_for(widget.get_toplevel())


      icon = dbox.render_icon(Gtk.STOCK_DIALOG_INFO, Gtk.IconSize.BUTTON)
      dbox.set_icon(icon)

      store = Gtk.ListStore(str, str, str, long)
      store.set_sort_column_id(CC_COL_IP, Gtk.SortType.ASCENDING)

      for ssh_conf in self.cmd_list:
        store.append([ssh_conf['ip'], ssh_conf['user'], ssh_conf['port'], long(ssh_conf['last_time'])])
      
      self.store = store
      self.ssh_filter = self.store.filter_new()
      self.ssh_filter.set_visible_func(self.ssh_filter_func)

      treeview = Gtk.TreeView(self.ssh_filter)
      selection = treeview.get_selection()
      selection.set_mode(Gtk.SelectionMode.SINGLE)
      selection.connect("changed", self.on_selection_changed, ui)
      ui['treeview'] = treeview

      renderer = Gtk.CellRendererText()
      column = Gtk.TreeViewColumn(_("Ip"), renderer, text=CC_COL_IP)
      treeview.append_column(column)

      renderer = Gtk.CellRendererText()
      column = Gtk.TreeViewColumn(_("User"), renderer, text=CC_COL_USER)
      treeview.append_column(column)

      renderer = Gtk.CellRendererText()
      column = Gtk.TreeViewColumn(_("Port"), renderer, text=CC_COL_PORT)
      treeview.append_column(column)

      scroll_window = Gtk.ScrolledWindow()
      scroll_window.set_size_request(500, 250)
      scroll_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
      scroll_window.add_with_viewport(treeview)

      search_entry = Gtk.SearchEntry()
      search_entry.connect("search_changed", self.on_search_changed)
      dbox.vbox.pack_start(search_entry, True, True, 0)

      hbox = Gtk.HBox()
      hbox.pack_start(scroll_window, True, True, 0)
      dbox.vbox.pack_start(hbox, True, True, 0)

      button_box = Gtk.VBox()

      button = Gtk.Button(_("New"))
      button_box.pack_start(button, False, True, 0)
      button.connect("clicked", self.on_new, ui) 
      ui['button_new'] = button

      button = Gtk.Button(_("Edit"))
      button_box.pack_start(button, False, True, 0)
      button.set_sensitive(False)
      button.connect("clicked", self.on_edit, ui) 
      ui['button_edit'] = button

      button = Gtk.Button(_("Delete"))
      button_box.pack_start(button, False, True, 0)
      button.connect("clicked", self.on_delete, ui) 
      button.set_sensitive(False)
      ui['button_delete'] = button

      button = Gtk.Button(_("SSH Connect"))
      button_box.pack_start(button, False, True, 0)
      button.connect("clicked", self.on_ssh_connect, ui) 
      button.set_sensitive(False)
      ui['button_ssh'] = button

      button = Gtk.Button(_("SFTP Connect"))
      button_box.pack_start(button, False, True, 0)
      button.connect("clicked", self.on_sftp_connect, ui) 
      button.set_sensitive(False)
      ui['button_sftp'] = button

      hbox.pack_start(button_box, False, True, 0)
      self.dbox = dbox
      dbox.show_all()
      res = dbox.run()
      if res == Gtk.ResponseType.ACCEPT:
        self.update_cmd_list(store)
        self._save_config()
      del(self.dbox)
      dbox.destroy()
      return

    def on_search_changed(self,entry):
        log_debug("on_search_changed")
        self.cur_input = entry.get_text()
        log_debug(self.cur_input)
        self.ssh_filter.refilter()
        
    def ssh_filter_func(self, model, iter, data):
        log_debug("ssh_filter_func")
        if self.cur_input is None or self.cur_input == "":
            return True
        else:
            return self.cur_input in model[iter][CC_COL_IP] or self.cur_input in model[iter][CC_COL_USER]

    def update_cmd_list(self, store):
        iter = store.get_iter_first()
        self.cmd_list = []
        i=0
        while iter:
          (ip, user, port,last_time) = store.get(iter,
                                              CC_COL_IP,
                                              CC_COL_USER,
                                              CC_COL_PORT,
                                              CC_COL_TIME)
          self.cmd_list.append({'ip' : ip,     'user': user, 
                                'port' : port, 'last_time':last_time})
          iter = store.iter_next(iter)
          i = i + 1

    def on_selection_changed(self,selection, data=None):
      treeview = selection.get_tree_view()
      (model, iter) = selection.get_selected()
      data['button_edit'].set_sensitive(iter is not None)
      data['button_delete'].set_sensitive(iter is not None)
      data['button_ssh'].set_sensitive(iter is not None)
      data['button_sftp'].set_sensitive(iter is not None)

    def _create_command_dialog(self, ip_var = "", user_var = "root", port_var = "22", passwd_var = ""):
      dialog = Gtk.Dialog(
                        _("New SSH Config"),
                        None,
                        Gtk.DialogFlags.MODAL,
                        (
                          _("_Cancel"), Gtk.ResponseType.REJECT,
                          _("_OK"), Gtk.ResponseType.ACCEPT
                        )
                      )
      dialog.set_transient_for(self.dbox)
      table = Gtk.Table(3, 2)

      label = Gtk.Label(label=_("Ip:"))
      table.attach(label, 0, 1, 0, 1)
      ip = Gtk.Entry()
      ip.set_text(ip_var)
      table.attach(ip, 1, 2, 0, 1)

      label = Gtk.Label(label=_("User:"))
      table.attach(label, 0, 1, 1, 2)
      user = Gtk.Entry()
      user.set_text(user_var)
      table.attach(user, 1, 2, 1, 2)
      
      label = Gtk.Label(label=_("Port:"))
      table.attach(label, 0, 1, 2, 3)
      port = Gtk.Entry()
      port.set_text(port_var)
      table.attach(port, 1, 2, 2, 3)

      label = Gtk.Label(label=_("Passwd:"))
      table.attach(label, 0, 1, 3, 4)
      passwd = Gtk.Entry()
      passwd.set_text(passwd_var)
      passwd.set_input_purpose(Gtk.InputPurpose.PASSWORD)
      passwd.set_visibility(False)
      passwd.set_invisible_char('*')
      table.attach(passwd, 1, 2, 3, 4)

      dialog.vbox.pack_start(table, True, True, 0)
      dialog.show_all()
      return (dialog,ip,user,port,passwd)

    def on_new(self, button, data):
      (dialog,ip,user,port,passwd) = self._create_command_dialog()
      res = dialog.run()
      item = {}
      if res == Gtk.ResponseType.ACCEPT:
        item['ip'] = ip.get_text()
        item['user'] = user.get_text()
        item['port'] = port.get_text()
        item['passwd'] = passwd.get_text()
        if item['ip'] == '' or item['user'] == '' or item['port'] == '':
          err = Gtk.MessageDialog(dialog,
                                  Gtk.DialogFlags.MODAL,
                                  Gtk.MessageType.ERROR,
                                  Gtk.ButtonsType.CLOSE,
                                  _("You need input ip and user and port")
                                )
          err.run()
          err.destroy()
        else:
          # we have a new command
          filter_store = data['treeview'].get_model()
          store = filter_store.get_model()

          ssh_key = item['user'] + '@' + item['ip']
          ssh_exist = False
          iter = store.get_iter_first()
          while iter != None:
            if store.get_path(iter) != store.get_path(iter) :
              temp_user = store.get_value(iter,CC_COL_USER)
              temp_ip = store.get_value(iter,CC_COL_IP)
              temp_key = temp_user + '@' + temp_ip

              if ssh_key == temp_key:
                ssh_exist = True
                break

            iter = store.iter_next(iter)
          if not ssh_exist:
            store.insert(0, (item['ip'], item['user'], item['port'], nowTime()))
            save_keyring_passwd(ssh_key, item['passwd'])
            self.update_cmd_list(store)
            self._save_config()
          else:
            gerr(_("ssh config *%s* already exist") % ssh_key)
      dialog.destroy()
 
    def on_delete(self, button, data):
      treeview = data['treeview']
      selection = treeview.get_selection()
      (store_filter, filter_iter) = selection.get_selected()

      store = store_filter.get_model()
      iter = store_filter.convert_iter_to_child_iter(filter_iter)
      if iter:
        ssh_key =  store.get_value(iter, CC_COL_USER) + '@' + store.get_value(iter, CC_COL_IP)
        delete_keyring_passwd(ssh_key)
        store.remove(iter)
        self.update_cmd_list(store)
        self._save_config()

      return
 
    def on_edit(self, button, data):
      treeview = data['treeview']
      selection = treeview.get_selection()
      (store_filter, filter_iter) = selection.get_selected()
      
      store = store_filter.get_model()
      iter = store_filter.convert_iter_to_child_iter(filter_iter)

      if not iter:
        return
      
      old_ssh_key =  store.get_value(iter, CC_COL_USER) + '@' + store.get_value(iter, CC_COL_IP)
      (dialog,ip,user,port,passwd) = self._create_command_dialog(
                                                ip_var = store.get_value(iter, CC_COL_IP),
                                                user_var = store.get_value(iter, CC_COL_USER),
                                                port_var = store.get_value(iter, CC_COL_PORT),
                                                passwd_var = get_keyring_passwd(old_ssh_key)
                                                                  )
      res = dialog.run()
      item = {}
      if res == Gtk.ResponseType.ACCEPT:
        item['ip'] = ip.get_text()
        item['user'] = user.get_text()
        item['port'] = port.get_text()
        item['passwd'] = passwd.get_text()
        if item['ip'] == '' or item['user'] == '' or item['port'] == '':
          err = Gtk.MessageDialog(dialog,
                                  Gtk.DialogFlags.MODAL,
                                  Gtk.MessageType.ERROR,
                                  Gtk.ButtonsType.CLOSE,
                                  _("You need input ip and user and port")
                                )
          err.run()
          err.destroy()
        else:
          ssh_key = item['user'] + '@' + item['ip']
          ssh_exist = False

          tmpiter = store.get_iter_first()
          while tmpiter != None:
            if store.get_path(tmpiter) != store.get_path(iter) :
              temp_user = store.get_value(tmpiter,CC_COL_USER)
              temp_ip = store.get_value(tmpiter,CC_COL_IP)
              temp_key = temp_user + '@' + temp_ip

              if ssh_key == temp_key:
                ssh_exist = True
                break

            tmpiter = store.iter_next(tmpiter)

          if not ssh_exist:
            store.set(iter,
                      CC_COL_IP,   item['ip'],
                      CC_COL_USER, item['user'],
                      CC_COL_PORT, item['port'],
                      CC_COL_TIME, nowTime()
                      )
            # 如果用户名和密码改变了则需要删除原来的密码
            if old_ssh_key != ssh_key:
              delete_keyring_passwd(old_ssh_key)
            save_keyring_passwd(ssh_key,item['passwd'])
            self.update_cmd_list(store)
            self._save_config()
          else:
            gerr(_("ssh config *%s* already exist") % ssh_key)

      dialog.destroy()
