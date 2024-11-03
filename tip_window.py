#!/usr/bin/env python3
# -*-coding: UTF-8 -*-

from gi.repository import Pango
import html
import uuid
import re

from terminatorlib.translation import _
from gi.repository import Vte
import gi
from gi.repository import Gtk, Gdk
from history import History

from utils import *
gi.require_version('Vte', '2.91')


start_blank = re.compile(r'^\s{2,}')

exclude_cmds = ["clear"]
SUGGESTION_NUM = 8         # 提示框展示的提示命令的最大数量

INTERVAL_LEVEL = [5000, 10000, 15000, 60000, 300000, 600000]

# VT100控制码 实验了多次，只支持这样，如\033[D， 而\033[15D这样的直接移动15个位置的不支持，原因未知
space_pattern = re.compile(r'\s')    # 用于匹配是否包含空白字符

(CC_COL_COMMAND, CC_COL_COUNT) = range(0, 2)

his_recorder = History()


class ListBoxRowWithData(Gtk.ListBoxRow):
    def __init__(self, data, back_len, start1, end1, start2,
                 pattern=None, back_size=0):
        super(Gtk.ListBoxRow, self).__init__()
        self.data = data
        self.back_len = back_len
        self.start1 = start1
        self.end1 = end1
        self.start2 = start2
        self.pattern = pattern
        self.back_size = back_size
        label = Gtk.Label()
        label.set_markup('<span foreground="blue">' + html.escape(data[:start1]) + '</span>'
                         + html.escape(data[start1:end1]) + '<span foreground="blue">' +
                         html.escape(data[end1:start2]) + '</span>' +
                         html.escape(data[start2:]))
        label.set_xalign(0)
        label.set_margin_start(5)
        label.set_margin_end(3)
        label.set_margin_top(3)
        label.set_margin_bottom(3)
        label.set_width_chars(15)
        label.set_max_width_chars(100)
        label.set_line_wrap(False)
        label.set_ellipsize(Pango.EllipsizeMode.END)

        self.add(label)


class TipWindow(Gtk.Window):

    # terminatorlib/terminal.py
    vte_version = Vte.get_minor_version()

    def __init__(self):

        self.recorder = {}
        self.terminal = None

        self.style_context = Gtk.StyleContext()
        self.provider = Gtk.CssProvider()

        self.provider.load_from_data(bytes("""
            #listbox {
                font-weight: 500;
            }
            """.encode()
        ))

        self.init_tip_window()

    def init_tip_window(self):

        tip_window = Gtk.Window(Gtk.WindowType.POPUP)
        tip_window.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        # tip_window.set_transient_for(self.parent)
        self.style_context.add_provider_for_screen(
            tip_window.get_screen(), self.provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        # listBox start
        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        listbox.unselect_all()
        listbox.set_name("listbox")

        def on_row_activated(listbox_widget, row):
            log_debug(row.data)
            back_len = row.back_len
            start1 = row.start1
            end1 = row.end1
            start2 = row.start2
            pattern = row.pattern
            back_size = row.back_size
            feed_cmd = row.data

            # 为了兼容zsh-autosuggestions这种，主动发一下 相当于 ↓ 这个按钮的控制码来消除zsh的自动提示
            if self.recorder[self.terminal].get("shell_auto_suggestion", False):
                down_control_seq = "\033[B"
                self.terminal.feed_child(down_control_seq.encode())

            if back_len > 0:
                back = "\033[D"
                self.terminal.feed_child((back * back_len).encode())
                self.terminal.feed_child(feed_cmd[start1:end1].encode())
                forward = "\033[C"
                self.terminal.feed_child((forward * back_len).encode())

            self.terminal.feed_child(feed_cmd[start2:].encode())

            if pattern is not None:
                if back_size > 0:
                    back = "\033[D"
                    self.terminal.feed_child((back * back_size).encode())
                self.recorder[self.terminal]["pattern"] = pattern

            self.recorder[self.terminal]["selected_suggestion"] = row.data
            self.hide_suggestion_list()

        listbox.connect('row-activated', on_row_activated)
        # listBox end
        self.listbox = listbox

        tip_window.add(listbox)
        tip_window.connect("key-press-event", self.tip_key_press)

        self.tip_window = tip_window
        self.tip_create_time = nowTime()
        self.wait_unselect = False
        self.wait_autoclose = False

    def start_record(self, terminal, t_terminal):
        log_debug("liangyong record start")

        # start
        (col, row) = terminal.get_cursor_position()
        self.recorder[terminal] = {"handler_id": 0, "min_col": 99999, "row": row,
                                   "line_start_row": row, "pre_cmd": "", "session": str(uuid.uuid1()),
                                   "hidden": False, "t_terminal": t_terminal}

        # 添加contents-changed事件的处理
        log_debug("liangyong record contents-changed")
        self.recorder[terminal]["handler_id"] = terminal.connect(
            'contents-changed', self.on_contents_change)

        self.recorder[terminal]["exit_id"] = terminal.connect(
            'child-exited', self.exit_record)

    def on_contents_change(self, terminal):
        log_debug("contents-changed")

        (col, row) = terminal.get_cursor_position()
        if col == 0 and row == 0:
            log_debug("this is the terminal start, return")
            return

        last_saved_row = self.recorder[terminal]["row"]
        if row != last_saved_row:
            if "clear_do_scrollbar_toggle" in self.recorder[terminal]:
                log_debug("clear_do_scrollbar_toggle")
                del self.recorder[terminal]["clear_do_scrollbar_toggle"]
                self.recorder[terminal]["t_terminal"].do_scrollbar_toggle()

        (col, row) = terminal.get_cursor_position()

        last_commited = self.recorder[terminal].get("commit_char", '')
        last_min_col = self.recorder[terminal].get("min_col", 9999)
        line_wrap = False  # 此次内容变化是否是长度导致的换行
        if row != last_saved_row:
            log_debug("row changed")
            log_debug(str(row) + ":" + str(last_saved_row))

            last_row_content = self.recorder[terminal].get("row_content", "")
            last_cmd = self.recorder[terminal].get("cmd_content", "")
            last_title = self.recorder[terminal].get("window_title", "")
            last_min_col = self.recorder[terminal]["min_col"]  # 光标最小位置
            log_debug(last_min_col)

            init_content = self.recorder[terminal].get("init_content", "")

            # 检查这次的内容变化是否是长度导致的换行/或者长度导致的行数减小
            # 需要判断row大于上一次的row，有过操作，且在三行内，才判断是不是换行，因为vi退出后也可能导致这种变化
            if row > last_saved_row and row - last_saved_row < 3 and self.recorder[terminal].get("has_operation", False):
                content_between_row = self.get_text_content_with_last(
                    terminal, last_saved_row, 0, row, terminal.get_column_count())
                log_debug(content_between_row)
                if content_between_row != '' and "\n" not in content_between_row:
                    line_wrap = True
            elif last_saved_row - row == 1:  # 本来跨行，后退后不跨了？
                line_wrap = True

            log_debug(line_wrap)
            if not line_wrap:  # 需要记录最后一条命令,并初始化新行
                # get output
                invi = self.recorder[terminal].get("invi", False)
                last_output = ''
                begin_row = last_saved_row + 1
                if row - 1 >= begin_row and not invi:  # vi模式中的不记录
                    if row-21 > begin_row:
                        begin_row = row-21
                    last_output, _ = self.get_text_content(
                        terminal, begin_row, 0, row-1, terminal.get_column_count())

                # last_output 可以用来提示
                log_debug(last_output)

                log_debug("print last content")
                log_debug(last_row_content)
                log_debug(init_content)
                log_debug(last_title)
                log_debug(last_cmd)
                log_debug(last_min_col)
                log_debug(last_commited)

                # 记录上次的命令行输入
                last_cmd = last_cmd.strip()
                line_start_row = self.recorder[terminal]["line_start_row"]
                invi = self.recorder[terminal].get("invi", False)

                if (last_min_col > 0 or line_start_row != last_saved_row) and not invi and init_content != last_row_content:
                    if start_blank.match(last_row_content):
                        log_debug(
                            "not record because row_content :" + last_row_content)
                    elif last_cmd != '' and len(last_cmd) > 1 and not last_cmd in exclude_cmds:
                        last_cmd = self.special_handle(last_cmd)
                        pre_cmd = self.recorder[terminal]["pre_cmd"]
                        index = last_row_content.find(last_cmd)
                        prefix = last_row_content[0:index].strip()
                        session = self.recorder[terminal]["session"]
                        # 记录2个命令之间的时间间隔
                        now = nowTime()
                        pre_time = self.recorder[terminal].get("pre_time", 0)
                        interval = now - pre_time
                        if interval >= 600000:
                            interval = -1

                        history = {"time": now, "prefix": prefix, "cmd": last_cmd, "window_title": last_title,
                                   "pre_cmd": pre_cmd, "session": session, "interval": interval}
                        log_debug(history)
                        his_recorder.add_history(history)
                        self.recorder[terminal]["pre_cmd"] = last_cmd
                        self.recorder[terminal]["pre_time"] = now

                        # 尝试提取公共部分
                        select_cmd = self.recorder[terminal].get(
                            "selected_suggestion", '')
                        select_pattern = self.recorder[terminal].get(
                            "pattern", None)
                        if select_cmd != '' and select_cmd != last_cmd and select_pattern is None:
                            log_debug("need try to get common")
                            common_cmd, back_size = get_common_cmd(
                                last_cmd, select_cmd)
                            # common_cmd 为空表示没有公共部分
                            if common_cmd != '':
                                his_recorder.append_common_cmd(
                                    common_cmd, back_size)
                        elif select_pattern is not None and select_pattern.match(last_cmd):
                            his_recorder.append_common_cmd(select_cmd, None)

                        self.recorder[terminal]["selected_suggestion"] = ''
                        self.recorder[terminal]["pattern"] = None

                    else:
                        if 'clear' == last_cmd:
                            self.recorder[terminal]["cmd_content"] = ""
                            # 从某个版本后出现clear后get_cursor_position不准确导致提示框位置异常，尝试了各种方法，发现将滚动条重新设置一遍后get_cursor_position恢复正常
                            # 因此这里主动触发一下do_scrollbar_toggle()，在on_contents_change里会判断并再次do_scrollbar_toggle()将滚动条设置恢复
                            self.recorder[terminal]["t_terminal"].do_scrollbar_toggle()
                            self.recorder[terminal]["clear_do_scrollbar_toggle"] = "true"
                        log_debug("not record because of last_cmd:" + last_cmd)

                # 初始化新行的相关变量
                self.init_new_row(terminal, col, row)

        self.recorder[terminal]["row"] = row
        if (last_min_col > col and self.recorder[terminal]["line_start_row"] == row):
            self.recorder[terminal]["min_col"] = col
        # 或者一开始是0，未进行过操作自动变到当前位置：对应场景是有时反应慢，过一会才正常显示在屏幕上
        elif last_min_col == 0 and not self.recorder[terminal]["has_operation"]:
            self.recorder[terminal]["min_col"] = col

        min_col = self.recorder[terminal]["min_col"]
        line_start_row = self.recorder[terminal]["line_start_row"]

        log_debug(last_commited)
        log_debug((line_start_row, row))
        log_debug(self.recorder[terminal].get("invi", False))

        # 为了修复跨行问题，多获取一行 这样也只能解决命令跨一行的问题，先这样，后面再看
        # 这里修复的场景是：变为第二行的首位，反应慢还没变到正常位置时
        row_content, _ = self.get_text_content(
            terminal, line_start_row, 0, row+1, terminal.get_column_count())
        lf_index = row_content.find("\n")
        if lf_index != -1:
            row_content = row_content[:lf_index]

        # 必须再次调用，不能直接由row_content得来,否则前面的PS1如zsh的含有特殊字符时，会出错
        cmd_content, cmd_attrs = self.get_text_content(
            terminal, line_start_row, min_col, row+1, terminal.get_column_count())
        lf_index = cmd_content.find("\n")
        if lf_index != -1:
            cmd_content = cmd_content[:lf_index]
            # cmd_content, cmd_attrs = cmd_content[:lf_index], cmd_attrs[:lf_index]
        # 对于如redis-cli,zsh这样的，后面可能会自动出现提示信息，这里尝试舍弃后面的部分
        # cmd_content = self.check_shell_auto_suggestion(terminal, cmd_content, cmd_attrs)

        self.recorder[terminal]["row_content"] = row_content
        self.recorder[terminal]["cmd_content"] = cmd_content
        self.recorder[terminal]["window_title"] = self.get_window_title(
            terminal)

        # 以下逻辑决定是否展示提示
        # 如果在单词级别提示下，则展示单词级别提示
        if terminal.is_focus():
            # 未跨行且最后输入的时单个字符并且不在vi下则提示
            if last_commited != '' and line_start_row == row and \
                    not self.recorder[terminal].get("invi", False) and \
                    not self.recorder[terminal]["hidden"]:
                self.show_tip_window(terminal)
            else:
                self.hide_suggestion_list()

    # 新版本cmd_attrs有变化，暂时无用了
    def check_shell_auto_suggestion(self, terminal, cmd_content, cmd_attrs):
        if len(cmd_attrs) > 0 and len(cmd_content) == len(cmd_attrs):
            first_fore = cmd_attrs[0].fore
            index = 0
            # log_debug((first_fore.blue,first_fore.green,first_fore.red))
            for attr in cmd_attrs:
                cur_for = attr.fore
                # log_debug((cur_for.blue,cur_for.green,cur_for.red,attr.column))
                # 根据颜色对比，如redis-cli,zsh这样使用VT100控制码浅色提示的，提示信息的颜色与前面的输入是不一样的
                if cur_for.blue != first_fore.blue or cur_for.green != first_fore.green or cur_for.red != first_fore.red:
                    # log_debug("color check break")
                    break
                index += 1

            if index < len(cmd_content):
                log_debug(
                    "may be have auto prompt string, such as redis-cli,zsh")
                self.recorder[terminal]["shell_auto_suggestion"] = True
                cmd_content = cmd_content[:index]
            else:
                self.recorder[terminal]["shell_auto_suggestion"] = False
        return cmd_content

    def init_new_row(self, terminal, col, row):
        log_debug("init_new_row")
        new_row_content, _ = self.get_text_content(
            terminal, row, 0, row, terminal.get_column_count())
        self.recorder[terminal]["min_col"] = col
        self.recorder[terminal]["commit_char"] = ''
        # if had operation(eg: move) on current line
        self.recorder[terminal]["has_operation"] = False
        self.recorder[terminal]["init_content"] = new_row_content
        self.recorder[terminal]["row_content"] = new_row_content
        self.recorder[terminal]["line_start_row"] = row
        self.recorder[terminal]["shell_auto_suggestion"] = False  #
        self.recorder[terminal]["selected_suggestion"] = ''
        self.recorder[terminal]["pattern"] = None
        self.recorder[terminal]["hidden"] = False
        self.check_if_invi(terminal)

    def check_if_invi(self, terminal):
        # 尝试检查是否在vi编辑中,不一定准确，但是没找到更好的办法
        adj = terminal.get_vadjustment()
        lower, upper, value, p_size = adj.get_lower(
        ), adj.get_upper(), adj.get_value(), adj.get_page_size()
        # log_debug(str(lower) +"/" + str(upper) +"/" + str(value) + "/" + str(p_size))
        # log_debug(terminal.get_row_count())
        if lower != 0 and lower == value and p_size == terminal.get_row_count():
            log_debug("now in vi")
            self.recorder[terminal]["invi"] = True
        else:
            self.recorder[terminal]["invi"] = False

    # Don't get the last char if is '\n'
    def get_text_content(self, terminal, start_row, start_col, end_row, end_col):
        if self.vte_version < 72:
            content_attr = terminal.get_text_range(start_row, start_col, end_row, end_col, lambda *a: True)
        else:
            content_attr = terminal.get_text_range_format(Vte.Format.TEXT, start_row, start_col, end_row, end_col)
        
        content = content_attr[0]
        attrs = content_attr[1]
        if content is None:
            return "", attrs
        if content.endswith("\n"):
            content = content[:-1]
            attrs = attrs
        return content, attrs

    def get_text_content_with_last(self, terminal, start_row, start_col, end_row, end_col):
        if self.vte_version < 72:
            content_attr = terminal.get_text_range(start_row, start_col, end_row, end_col, lambda *a: True)
        else:
            content_attr = terminal.get_text_range_format(Vte.Format.TEXT, start_row, start_col, end_row, end_col)
        content = content_attr[0]
        if content is None:
            return ""
        return content

    # title有可能取空
    def get_window_title(self, terminal):
        title = terminal.get_window_title()
        if title is None:
            title = ''
        return title

    # 当前仅对cd 进行特殊处理，统一去掉最后的/
    def special_handle(self, last_cmd):
        if last_cmd.startswith("cd ") and last_cmd.endswith("/"):
            last_cmd = last_cmd.rstrip("/")

        # 普通的命令，中间有多个空格的，替换为1个
        if not '"' in last_cmd and not "'" in last_cmd:
            last_cmd = " ".join(last_cmd.split())

        return last_cmd

    def exit_record(self, terminal, status):
        log_debug("exit_record")
        his_recorder.append_to_histable()
        if terminal in self.recorder:
            if self.recorder[terminal].get("handler_id", 0) != 0:
                terminal.disconnect(self.recorder[terminal]["handler_id"])
            del (self.recorder[terminal])

        # 如果所有窗口都关闭了，则关闭连接
        if len(self.recorder) == 0:
            try:
                his_recorder.conn.close()
            except:
                log_debug("close slite exception")
            else:
                log_debug("close sqlite success")

        # 在commit事件并且content发生变化后展示提示
    def show_tip_window(self, terminal):
        col, row = terminal.get_cursor_position()

        min_col = self.recorder[terminal]["min_col"]
        current_cmd, _ = self.get_text_content(
            terminal, row, min_col, row, col)
        current_cmd = current_cmd.lstrip()
        log_debug(current_cmd)  # 获取当前的输入用于提示

        # 如果是空的，则不提示
        if current_cmd == '':
            return

        self.reshow_suggestion_list(terminal, current_cmd)

        # 计算光标在屏幕上的绝对位置
    def get_screen_cursor_postition(self, terminal):
        # 获取光标的行列
        (col, row) = terminal.get_cursor_position()
        # clear之后row是错误的，且无法回滚，导致计算出错
        log_debug("get_cursor_position", col, row)

        adj = terminal.get_vadjustment()  # 获取滚动条
        log_debug(adj.get_value(), adj.get_lower(), adj.get_upper())

        show_row = row - adj.get_value()  # row减去滚动条值，则是 terminal 可见区看见的行数
        log_debug("show_row", show_row)
        width = terminal.get_char_width()  # 字符宽度
        height = terminal.get_char_height()  # 字符高度
        x, y = col * width, show_row * height   # terminal上光标所在位置
        log_debug(x, y)

        x1, y1 = terminal.translate_coordinates(terminal.get_toplevel(),x,y)  # 光标在terminal的顶级窗口的位置

        gdk_p = Gdk.Window.get_origin(terminal.get_toplevel().get_window())  # terminal顶级窗口在屏幕上的位置

        return x1 + gdk_p.x, y1 + gdk_p.y

    # 计算提示框应该展示的坐标位置
    def get_tip_position(self, terminal, current_cmd):
        width = terminal.get_char_width()  # 字符宽度
        height = terminal.get_char_height()  # 字符高度
        # 获取光标的坐标
        screen_x, screen_y = self.get_screen_cursor_postition(terminal)
        log_debug(screen_x, screen_y)
        # 提示框应该出现的位置 光标在屏幕上的x, 减去 当前输入的宽度 再减去 ListBoxRow 的 margin
        showx, showy = screen_x - \
            len(current_cmd) * width - 5, screen_y + height
        return showx, showy

    def ensure_tip_inscreen(self, terminal, showx, showy):
        # 计算提示框出现的位置，如果超出了屏幕则显示在上方
        position_changed = False

        screen = terminal.get_window().get_screen()
        screen_w = screen.get_width()
        screen_h = screen.get_height()  # 屏幕高度

        gdk_p = Gdk.Window.get_origin(
            terminal.get_toplevel().get_window())  # terminal顶级窗口在屏幕上的位置
        log_debug(gdk_p)

        tip_w, tip_h = self.tip_window.get_size()
        if showy + tip_h > screen_h:
            log_debug("show_at_above " + str(tip_h))
            position_changed = True
            showy = showy - tip_h - terminal.get_char_height()

        if position_changed:
            log_debug("position_changed " + str(showx) + ":" + str(showy))
            self.tip_window.move(showx, showy)
            self.tip_window.set_opacity(0.8)  # 设置透明度
        else:
            log_debug("show_at " + str(showx) + ":" + str(showy))
            self.tip_window.set_opacity(0.7)

    # 匹配三种情况
    # 1、startswith: 'cd b'' 匹配 'cd bin'
    # 2、中间模糊匹配   'bi' 匹配 'cd bin'
    # 3、命令完全匹配，变量模糊匹配  'cd i' 匹配 'cd bin'
    # 第2和第3种场景为了避免出现如cd 匹配到 git commitid: 384fb8b63bcd1abb37fa09d2416aeb155e57fac6 的这种情况 用正则进行过滤
    def find_match(self, cur_input, cur_cmd, cur_args, cur_pattern, his_cmd):
        input_len = len(cur_input)
        if his_cmd.startswith(cur_input):
            return 0, 0, 0, input_len

        # 当输入长度大于等于2,小于等于20时，才进行模糊匹配
        if input_len < 2 or input_len > 20:
            return -1, -1, -1, -1

        matchObj = cur_pattern.search(his_cmd)
        if matchObj:
            match_region = matchObj.span(3)
            in_word = matchObj.group(2)
            # log_debug(in_word)
            # 如果 匹配到的部分在一个单词中，单词前后其他部分小于等于20才认为是匹配，用于过滤 模糊匹配到 git 的 commitid等情况
            if len(in_word) - input_len <= 20:
                return len(cur_args), len(cur_cmd), match_region[0], match_region[1]

        return -1, -1, -1, -1

    def reshow_suggestion_list(self, terminal, cur_input):
        log_debug("reshow_suggestion_list:" + cur_input)

        # 销毁后重建提示框
        self.tip_window.destroy()
        self.init_tip_window()
        self.tip_window.override_font(terminal.get_font().copy())

        select_pattern = self.recorder[terminal].get("pattern", None)
        if select_pattern is not None and select_pattern.match(cur_input):
            log_info("in common cmd mode")
            return
        else:
            self.recorder[terminal]["pattern"] = None

        # 将要添加的提示
        list_add = []

        # 如果时不带空格的情况下 x是-1，则args是全部input
        x = cur_input.find(' ')
        cur_cmd = cur_input[:x+1]
        cur_args = cur_input[x+1:]
        cur_pattern = re.compile(
            r'^' + re.escape(cur_cmd) + r'(.*\W)?(\w*(' + re.escape(cur_args) + r')\w*)((\W.*)|$)', re.U)

        # 获取当前的 title pre_cmd prefix 用于计算
        title = self.get_window_title(terminal)
        pre_cmd = self.recorder[terminal]["pre_cmd"]

        row_content = self.recorder[terminal]["row_content"]
        min_col = self.recorder[terminal]["min_col"]
        prefix = row_content[0:min_col].strip()

        for cmd, stat in his_recorder.history_stat.items():
            if cmd == cur_input:
                continue

            back_len, start1, end1, start2 = self.find_match(
                cur_input, cur_cmd, cur_args, cur_pattern, cmd)
            if back_len >= 0 and len(cmd) > 3:
                self.calculate_and_add(
                    terminal, title, pre_cmd, prefix, cmd, stat, back_len, start1, end1, start2, list_add)

        # 根据总得分排序，最多取前十个
        list_add.sort(key=by_score, reverse=True)
        max_num = int(round(SUGGESTION_NUM * 1.5))
        if len(list_add) > max_num:
            list_add = list_add[:max_num]

        self.process_common(cur_input, list_add)

        # 根据总得分排序，最多取前十个
        list_add.sort(key=by_score, reverse=True)
        if len(list_add) > SUGGESTION_NUM:
            list_add = list_add[:SUGGESTION_NUM]

        log_debug(list_add)
        for suggest_cmd in list_add:
            # 这里 back_len 是用于模糊匹配时回退多少个字符才开始输入第一段的
            # back_size 是表示 如果是公共命令，输入进去后需要回退多少个字符来输入其他部分
            self.listbox.add(ListBoxRowWithData(suggest_cmd["cmd"], suggest_cmd["back_len"],
                                                suggest_cmd["start1"], suggest_cmd["end1"],
                                                suggest_cmd["start2"], suggest_cmd.get("pattern", None), suggest_cmd.get("back_size", 0))
                             )

        if len(self.listbox.get_children()) == 0:
            log_debug("return")
            return

        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)

        showx, showy = self.get_tip_position(terminal, cur_input)

        self.tip_window.move(showx, showy)
        self.tip_window.show_all()

        # 计算提示框出现的位置，如果超出了屏幕则显示在上方
        self.ensure_tip_inscreen(terminal, showx, showy)
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.terminal = terminal

    # 计算得分 cmd是历史命令，stat是统计数据 back_len是需要回退的长度，end1是第一段输入结束的索引 start2是第二段输入开始的索引 list_add是加入的提示列表
    def calculate_and_add(self, terminal, title, pre_cmd, prefix, cmd, stat, back_len, start1, end1, start2, list_add):

        score = 0
        count = stat["count"]
        last_time = stat["last_time"]  # not used now

        # 计算分数  使用 x/(base+x)
        title_match, title_count, title_relation = max_match_str(
            str(title), stat["titles"])

        title_score = title_match * (float(title_count)/(1 + title_count))

        # pre_cmds转化为 {"cmd":total_count} 的结构 再去匹配
        to_match_precmds = {}
        for precmd, intervals in stat["pre_cmds"].items():
            precmd_count = 0
            for interval, _count in intervals.items():
                # 间隔没有意义的不管了 interval的值参见 get_interval_level
                if interval >= 10:
                    continue
                precmd_count = precmd_count + _count

            to_match_precmds[precmd] = precmd_count

        pre_cmd_match, pre_cmd_count, pre_cmd_relation = max_match_str(
            str(pre_cmd), to_match_precmds, 2)

        precmd_score = pre_cmd_match * \
            (float(pre_cmd_count)/(1 + pre_cmd_count))

        prefix_match, prefix_count, prefix_relation = max_match_str(
            str(prefix), stat["prefixs"], 1)

        prefix_score = prefix_match * (float(prefix_count)/(3 + prefix_count))

        phase1_score = 3 * (title_score * title_relation + precmd_score * pre_cmd_relation + prefix_score * prefix_relation)\
            / (title_relation + pre_cmd_relation + prefix_relation)

        now = nowTime()
        period = now - last_time  # 毫秒
        day_period = float(period) / (24 * 3600 * 1000)
        week_period = float(day_period) / 7
        if week_period < 0.3:
            week_period = 0.3
        if week_period > 4:
            week_period = 4
        # period_score 的值范围将是 0.23 -- 0.8 之间  间距 0.57
        # 最后得分减去时间分数，以此来用于将很久之前的历史命令降低排名,待观察是否必要
        period_score = float(week_period)/(1 + week_period)

        # 总的数量 count_score 值范围是 0.5 到 1 之间 暂无用
        count_score = float(count)/(1 + count)
        score = phase1_score - period_score

        if DEBUG_ENABLE:
            append_result = {"cmd": cmd, "phase1_score": phase1_score, "score": score,
                             "title_match": title_match, "title_count": title_count, "title_score": title_score,
                             "pre_cmd_match": pre_cmd_match, "pre_cmd_count": pre_cmd_count, "precmd_score": precmd_score,
                             "prefix_match": prefix_match, "prefix_count": prefix_count, "prefix_score": prefix_score,
                             "period_score": period_score, "count": count, "count_score": count_score,
                             "back_len": back_len, "start1": start1, "end1": end1, "start2": start2}
        else:
            append_result = {"cmd": cmd, "phase1_score": phase1_score, "count": count,
                             "period_score": period_score, "score": score, "back_len": back_len,
                             "start1": start1, "end1": end1, "start2": start2}

        list_add.append(append_result)

    # 处理当前的list_add是否有公共命令
    def process_common(self, cur_input, list_add):
        log_debug("process_common")
        # 如果时不带空格的情况下 x是-1，则args是全部input
        x = cur_input.find(' ')
        cur_cmd = cur_input[:x+1]
        cur_args = cur_input[x+1:]
        cur_pattern = re.compile(
            r'^' + re.escape(cur_cmd) + r'(.*\W)?(\w*(' + re.escape(cur_args) + r')\w*)((\W.*)|$)')

        matched_commons = []
        for common_cmd in his_recorder.all_common_cmds:
            back_len, start1, end1, start2 = self.find_match(
                cur_input, cur_cmd, cur_args, cur_pattern, common_cmd["cmd"])
            if back_len < 0 or common_cmd["cmd"] == cur_input:
                continue

            # 给公共命令组装 编译后的 正则
            common_pattern = common_cmd.get("pattern", None)
            if common_pattern is None:
                common_str = common_cmd["cmd"]
                back_size = common_cmd["back_size"]
                index = len(common_str) - back_size
                common_pattern = re.compile(
                    r'' + common_str[:index] + r'[^ ]*' + common_str[index:])
                common_cmd["pattern"] = common_pattern

            item = common_cmd.copy()
            item.update({"back_len": back_len, "start1": start1,
                        "end1": end1, "start2": start2})
            matched_commons.append(item)

        final_commons = {}   # 最终匹配到的公共部分
        for suggest_cmd in list_add:
            _cmd_str = suggest_cmd["cmd"]
            _count = suggest_cmd["count"]
            if '"' in _cmd_str or "'" in _cmd_str:
                continue
            s1 = _cmd_str.split()
            if len(s1) < 3:
                continue

            for item in matched_commons:
                common_pattern = item["pattern"]
                _common_cmd_str = item["cmd"]

                if common_pattern.match(_cmd_str):
                    phase1_score = suggest_cmd["phase1_score"]
                    item["phase1_score"] = item.get(
                        "phase1_score", 0) + phase1_score
                    # 计算匹配此公共的suggest个数，用于最后计算平均值
                    item["m_count"] = item.get("m_count", 0) + 1
                    item["_count_sum"] = item.get(
                        "_count_sum", 0) + _count  # 用于最后计算count平均值
                    final_commons[_common_cmd_str] = item
                    # 不break的原因是比如 有 ps -ef|grep xxx1 ps -ef|grep xxx2
                    # 公共部分可能提取到 ps -ef|grep 也有 ps -ef|grep xxx
                    # break的话就只能匹配到一个了
                    # break

        for _, common in final_commons.items():
            m_count = common["m_count"]
            average_count = float(
                common["_count_sum"] + common["count"])/m_count

            common["phase1_score"] = common["phase1_score"]/m_count
            count_score = float(average_count)/(4 + average_count)
            common["score"] = common["phase1_score"] + count_score
            common["common"] = True  # 表示是公共部分

            list_add.append(common)

        log_debug(final_commons)

    def hide_suggestion_list(self):
        log_debug("hide_suggestion_list")
        self.listbox.unselect_all()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.wait_unselect = False
        self.wait_autoclose = False
        self.tip_window.hide()

    def terminal_keypress(self, terminal, event):
        log_debug("terminal_keypress")

        self.recorder[terminal]["has_operation"] = True
        key = Gdk.keyval_name(event.keyval)
        log_debug(key)
        log_debug(event.state)
        is_shift_mask = (
            event.get_state() & Gdk.ModifierType.SHIFT_MASK == Gdk.ModifierType.SHIFT_MASK)

        # 如果时shift+空格，则临时关闭此行的自动提示
        if is_shift_mask and key == 'space':
            log_debug("close tip_window")
            self.recorder[terminal]["hidden"] = not self.recorder[terminal]["hidden"]
            self.hide_suggestion_list()
            return True

        if self.tip_window.is_visible():
            if self.listbox.get_selected_row() and (key == 'Down' or key == 'Up' or key == 'Return'):
                new_event = Gdk.Event.copy(event)
                self.tip_window.emit("key-press-event", new_event)
                return True   # stop event handle by other handlers

            if not self.listbox.get_selected_row():
                if key == 'Down':
                    log_debug("active and set first row")
                    self.listbox.select_row(self.listbox.get_row_at_index(0))
                    self.listbox.get_row_at_index(0).grab_focus()
                    return True  # stop event handle by other handlers

                if key == 'Up':
                    log_debug("active and set last row")
                    max_index = len(self.listbox.get_children()) - 1
                    self.listbox.select_row(
                        self.listbox.get_row_at_index(max_index))
                    self.listbox.get_row_at_index(max_index).grab_focus()
                    return True  # stop event handle by other handlers

    def tip_key_press(self, tip_window, event):
        log_debug("tip_key_press")
        # 只要提示框一有任何操作则 自动关闭和自动取消选中第一条设置为 False
        self.wait_unselect = False
        self.wait_autoclose = False
        key = Gdk.keyval_name(event.keyval)
        if key != 'Down' and key != 'Up' and key != 'Return':
            self.hide_suggestion_list()

            new_event = Gdk.Event.copy(event)
            self.terminal.emit("key-press-event", new_event)

        max_index = len(self.listbox.get_children()) - 1
        first_row = self.listbox.get_row_at_index(0)
        last_row = self.listbox.get_row_at_index(max_index)
        select_row = self.listbox.get_selected_row()

        if key == 'Down':
            if select_row == last_row:
                log_debug("active and set first row")
                self.listbox.select_row(first_row)
                first_row.grab_focus()
                return True  # stop event handle by other handlers
        elif key == "Up":
            if select_row == first_row:
                log_debug("active and set last row")
                self.listbox.select_row(last_row)
                last_row.grab_focus()
                return True  # stop event handle by other handlers

    # tip窗口展示的逻辑，首先commmit触发 然后content-change后展示提示框
    def terminal_commit(self, terminal, text, size):
        log_debug("terminal_commit:" + str(text) + ":" + str(size))

        accept = False
        if size == 1 and (32 <= ord(text[0]) <= 126):
            accept = True
            self.recorder[terminal]["commit_char"] = text[0]

        if not accept:
            self.recorder[terminal]["commit_char"] = ''
            if self.tip_window.is_visible():
                self.hide_suggestion_list()
    # 丢失焦点

    def focus_out(self, terminal, event):
        log_debug("focus_out")
        self.tip_window.hide()

    def start_for_terminal(self, terminal, t_terminal):
        log_debug("start_for_terminal " + str(terminal))
        terminal.connect("key-press-event", self.terminal_keypress)
        terminal.connect_after("commit", self.terminal_commit)
        terminal.connect("focus-out-event", self.focus_out)

        self.start_record(terminal, t_terminal)

    # 展示窗口用于删除部分错误命令 代码框架复制于 terminatorlib/plugins/custom_commands.py
    def open_his_view(self, widget, data=None):
        log_debug("show history view")
        # 先把缓存的命令输入到历史
        his_recorder.append_to_histable()

        ui = {}
        dbox = Gtk.Dialog(
            _("Commands History"),
            None,
            Gtk.DialogFlags.MODAL,
            (
                _("_OK"), Gtk.ResponseType.ACCEPT
            )
        )
        dbox.set_transient_for(widget.get_toplevel())

        icon = dbox.render_icon(Gtk.STOCK_DIALOG_INFO, Gtk.IconSize.BUTTON)
        dbox.set_icon(icon)

        store = Gtk.ListStore(str, int)
        self.store = store

        cmd_list = his_recorder.get_lfu_cmds()
        for command in cmd_list:
            store.append([command['command'], command['count']])

        treeview = Gtk.TreeView(store)
        # treeview.connect("cursor-changed", self.on_cursor_changed, ui)
        selection = treeview.get_selection()
        selection.set_mode(Gtk.SelectionMode.SINGLE)
        selection.connect("changed", self.on_selection_changed, ui)
        ui['treeview'] = treeview

        renderer = Gtk.CellRendererText()
        column = Gtk.TreeViewColumn(
            _("Command"), renderer, text=CC_COL_COMMAND)
        column.set_fixed_width(420)
        column.set_max_width(420)
        column.set_expand(True)
        treeview.append_column(column)

        renderer = Gtk.CellRendererText()
        column = Gtk.TreeViewColumn(_("Count"), renderer, text=CC_COL_COUNT)
        treeview.append_column(column)

        scroll_window = Gtk.ScrolledWindow()
        scroll_window.set_size_request(500, 250)
        scroll_window.set_policy(
            Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll_window.add_with_viewport(treeview)

        search_entry = Gtk.SearchEntry()
        search_entry.connect("search_changed", self.on_search_changed)
        dbox.vbox.pack_start(search_entry, True, True, 0)

        hbox = Gtk.HBox()
        hbox.pack_start(scroll_window, True, True, 0)
        dbox.vbox.pack_start(hbox, True, True, 0)

        button_box = Gtk.VBox()

        button = Gtk.Button(_("Delete"))
        button_box.pack_start(button, False, True, 0)
        button.connect("clicked", self.on_delete, ui)
        button.set_sensitive(False)
        ui['button_delete'] = button

        hbox.pack_start(button_box, False, True, 0)
        self.dbox = dbox
        dbox.show_all()
        res = dbox.run()
        if res == Gtk.ResponseType.ACCEPT:
            pass
        del (self.dbox)
        dbox.destroy()
        return

    def on_selection_changed(self, selection, data=None):
        log_debug("on_selection_changed")
        treeview = selection.get_tree_view()
        (model, iter) = selection.get_selected()
        # log_debug(model.get_value(iter,0))
        data['button_delete'].set_sensitive(iter is not None)

    def on_search_changed(self, entry):
        log_debug("on_search_changed")
        input = entry.get_text()

        cmd_list = his_recorder.get_lfu_cmds(input)
        self.store.clear()

        for command in cmd_list:
            self.store.append([command['command'], command['count']])

    def on_delete(self, button, data):
        log_debug("on_delete")
        treeview = data['treeview']
        selection = treeview.get_selection()
        (store, iter) = selection.get_selected()
        if iter:
            del_cmd = store.get_value(iter, 0)
            log_debug(del_cmd)
            # 删除相关 history 和 history_stat
            his_recorder.delete_cmd(del_cmd)
            store.remove(iter)
