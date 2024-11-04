# Plugin by liangyong <1035367029@qq.com>
# See LICENSE of Terminator package.

""" tip.py - Terminator Plugin to show suggesstion of individual
terminals """

from gi.repository import Gtk
import terminatorlib.plugin as plugin
from terminatorlib.translation import _
from terminatorlib.terminator import Terminator

from tip_window import TipWindow

AVAILABLE = ['Tip']

tip_window = TipWindow()


def on_show(terminal):
    tip_window.start_for_terminal(terminal.get_vte(), terminal)


def new_register_terminal(self, terminal):
    Terminator.old_register_terminal(self, terminal)
    terminal.connect("show", on_show)


class Tip(plugin.MenuItem):

    def __init__(self):

        plugin.MenuItem.__init__(self)

        for term in Terminator().terminals:
            tip_window.start_for_terminal(term.get_vte(), term)

        # 修改terminator的register_terminal方法，在注册后执行 tip.start_for_terminal(term.get_vte())
        if not hasattr(Terminator, "old_register_terminal"):
            Terminator.old_register_terminal = Terminator.register_terminal
            Terminator.register_terminal = new_register_terminal

    def callback(self, menuitems, menu, terminal):
        """ Add save menu item to the menu"""
        item = Gtk.MenuItem.new_with_mnemonic(_('View _History'))
        item.connect("activate", tip_window.open_his_view)
        menu.append(item)
