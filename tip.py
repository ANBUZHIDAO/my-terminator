#!/usr/bin/env python
#-*-coding: UTF-8 -*-

import gi
from gi.repository import Gtk,Gdk
gi.require_version('Vte', '2.91')
from gi.repository import Vte
from gi.repository import GLib,Pango
from terminatorlib.translation import _

import os
import sys
reload(sys)
sys.setdefaultencoding('utf8')
import datetime,time
import json
import re
import sqlite3
import uuid
import math
import cgi
from collections import OrderedDict

def adapt_datetime(ts):
    return time.mktime(ts.timetuple())

sqlite3.register_adapter(datetime.datetime, adapt_datetime)

nowTime = lambda:int(round(time.time() * 1000))
db_file = os.path.join(os.path.expanduser('~'), '.terminator.db')
start_blank = re.compile("^\s{2,}")
exclude_cmds = ["clear"]
SUGGESTION_NUM = 8         # 提示框展示的提示命令的最大数量
MAX_HIS_NUM = 8000          # history记录数大于此数时，删除前 DELETE_NUM条。出现性能问题后可先把这个值调小，再想其他办法解决
DELETE_NUM = 2000           # 删除时删除前多少条
MAX_STAT_NUM = 800          # cmd数量最大800，多出的删除。出现性能问题后可先把这个值调小，再想其他办法解决
DELETE_STAT_NUM = 200       # stat删除时删除前多少条
AUTO_TIP_WAIT = 3500        # 自动提示显示多久自动关闭
AUTO_SELECT_ENABLE = False  # 开启后 会选中第一条提示，但是比如想执行 ab, 自动选中了 abc,此时回车会输入abc，无法执行 ab
AUTO_SELECT_WAIT = 1500     # 自动选中第一条提示命令后多久自动取消选中,---废弃不用了 不确定性的东西会导致经常出错
DEBUG_ENABLE = False         # 是否打印DEBUG日志
INTERVAL_LEVEL = [5000,10000,15000,60000,300000,600000]

# VT100控制码 实验了多次，只支持这样，如\033[D， 而\033[15D这样的直接移动15个位置的不支持，原因未知

cmd_pattern = re.compile(r'[^ ]+ (/)?')
space_pattern = re.compile(r'\s')    # 用于匹配是否包含空白字符

(CC_COL_COMMAND, CC_COL_COUNT) = range(0,2)

def log_debug(msg):
    if DEBUG_ENABLE:
        print("[DEBUG]: %s (%s:%d)" % (msg, __file__[__file__.rfind('/')+1:], sys._getframe(1).f_lineno))

def log_info(msg):
    print '\033[32m' + msg + "\033[0m"

def timing(f):
    def wrap(*args):
        time1 = time.time()
        ret = f(*args)
        time2 = time.time()
        print '%s function took %0.5f ms' % (f.func_name, (time2-time1)*1000.0)
        return ret
    return wrap

class LRUCache(OrderedDict):
    def __init__(self, size=128):
        self.size = size,
        self.cache = OrderedDict()
 
    def get(self, key):
        if self.cache.has_key(key):
            val = self.cache.pop(key)
            self.cache[key] = val
        else:
            val = None
 
        return val
 
    def set(self, key, val):
        if self.cache.has_key(key):
            val = self.cache.pop(key)
            self.cache[key] = val
        else:
            if len(self.cache) == self.size:
                self.cache.popitem(last=False)
                self.cache[key] = val
            else:
                self.cache[key] = val

def split_word(str):
    s_list = re.split("\W+",str)

    for i in range(len(s_list)-1, -1, -1):
        if s_list[i] == '':
            s_list.pop(i)
    
    return s_list

# simple two string match
str_match_cache = LRUCache(size=2000)
#@timing
def match_str_by_words(str1, str2): 
    key = str1+":"+str2
    cache_match =  str_match_cache.get(key) 
    if cache_match is not None:
        return cache_match

    l_match = left_match_str_by_words(str1, str2)
    if l_match >= 0.5:
        str_match_cache.set(key, l_match)
        return l_match
        
    r_match = right_match_str_by_words(str1, str2)
    bigger_match = (l_match if(l_match > r_match) else r_match)
    str_match_cache.set(key, bigger_match)
    return bigger_match

def left_match_str(str1, str2):
    i,j = len(str1),len(str2)
    k = (i if(i<j) else j)
    if k == 0:
        return 0

    m = 0
    while m < k:
        if str1[m] != str2[m]:
            break
        m = m + 1
        
    l_match = float(2*m)/(i+j)
    return l_match

def left_match_str_by_words(str1, str2):

    if len(str1) < 7 or len(str2) < 7:
       return  left_match_str(str1, str2)

    s1 = split_word(str1)
    s2 = split_word(str2)

    size1 = len(s1)
    size2 = len(s2)

    k = (size1 if(size1<size2) else size2)
    if k == 0:
        return 0

    m = 0
    while m < k:
        if s1[m] != s2[m]:
            break
        m = m + 1
        
    l_match = float(2*m)/(size1+size2)
    return l_match

def right_match_str(str1, str2):
    i,j = len(str1),len(str2)
    k = (i if(i<j) else j)
    if k == 0:
        return 0

    m = 0 
    while m < k: 
        if str1[i-1-m] != str2[j-1-m]:
            break
        m = m + 1 
        
    r_match = float(2*m)/(i+j)
    return r_match

def right_match_str_by_words(str1, str2):

    if len(str1) < 7 or len(str2) < 7:
       return  right_match_str(str1, str2)

    s1 = split_word(str1)
    s2 = split_word(str2)

    size1 = len(s1)
    size2 = len(s2)

    k = (size1 if(size1<size2) else size2)
    if k == 0:
        return 0

    m = 0
    while m < k:
        if s1[size1-1-m] != s2[size2-1-m]:
            break
        m = m + 1
        
    r_match = float(2*m)/(size1+size2)
    return r_match

p_match_cache = LRUCache(size=2000)
#@timing
def prefix_match_str(str1, str2):
    key = str1+":"+str2
    cache_match =  p_match_cache.get(key) 
    if cache_match is not None:
        return cache_match

    #prefix是从后往前匹配，且去掉尾部可能存在的特殊字符
    prefix_match = right_match_str_by_words(str1,str2)
    p_match_cache.set(key, prefix_match)
    return prefix_match

#@timing
def precmd_match_str(str1, str2):

    s1 = str1.split()
    s2 = str2.split()

    if len(s1) != len(s2):
        return 0
    else:
        if len(s1) == 1:
            return 0
        elif len(s1) == 2:
           # 主要为了区别 第二个参数带/和不带/ 如 cd /data 和 cd data
            matchObj1 = cmd_pattern.match(str1)
            matchObj2 = cmd_pattern.match(str2)
            if matchObj1 and matchObj2:
                cmd1 = matchObj1.group()
                cmd2 = matchObj2.group()
                if cmd1 != cmd2:
                    return 0
                else:
                    return 0.3
        else:
            common_cmd,_ = get_common_cmd(str1,str2)
            if common_cmd != '':
                return 0.8
            else:
                return 0 

    return 0

# type: 1:prefix 2:precmd
def max_match_str(str1, strmaps, type=None):
    max_match = 0
    max_match_count = 0
    total = 0   #总数
    kinds = 0   #种类

    for str2,count in strmaps.items():
        total = total + count
        kinds = kinds + 1

        if str1 == str2:
            new_match = 1
        elif str1 is None or str2 is None:
            new_match = 0
        elif type == 1:
            new_match = prefix_match_str(str1, str2)
        elif type == 2:
            new_match = precmd_match_str(str1, str2)
        else:
            new_match = match_str_by_words(str1, str2)  # title 的匹配

        if new_match > max_match:
            max_match = new_match
            max_match_count = count

        # 为了计算总数，这里不 break
        # if max_match == 1:
        #     break

    # 计算相关度
    relation = float(1)/kinds
    if kinds < 5 and max_match_count > 0:
        relation = float(max_match_count)/total

    return max_match, max_match_count, relation

# 尝试提取2个命令的公共部分， 
# 如 git cherry-pick aaksdasd0102021 -n和git cherry-pick isidasudusaudau -n 得到 (git cherry-pick  -n, 3)
# 3表示公共部分中不同的部分在中间的情况，最后公共部分输入到终端后需要 回退3列来输入不同的部分
def get_common_cmd(str1,str2):

    if '"' in str1 or "'" in str1 or '"' in str2 or "'" in str2:
        return '',0

    s1 = str1.split()
    s2 = str2.split()

    if len(s1) != len(s2) or len(s1) < 3 or s1[0] != s2[0]:
        return '',0

    not_match_count = 0
    not_match_index = -1   #不一样的部分的索引
    for index, substr in enumerate(s1):
        substr2 = s2[index]

        if substr != substr2:
            not_match_count = not_match_count + 1
            not_match_index = index
        
        if not_match_count > 1:
            break
    #不同的部分不等于1 则不处理
    if not_match_count == 1:
        common_prefix = os.path.commonprefix([s1[not_match_index],s2[not_match_index]])
        s1[not_match_index] = common_prefix
        common_cmd = " ".join(s1)
        return common_cmd, len(" ".join(s1[not_match_index:])) - len(common_prefix)
    
    return '',0

def get_interval_level(interval):
    if interval == -1 or not interval:
        return 10
    for level, value in enumerate(INTERVAL_LEVEL):
        if interval <= value:
            return level

def by_score(suggest_cmd):
    return suggest_cmd["score"]

class History():
    def __init__(self):
        self.new_history =[]
        self.last_history = {"cmd":""}
        self.total_count = 0
        self.history_stat = {}
        self.init_history()
        self.last_append_time = nowTime()

    def init_history(self):
        self.conn = sqlite3.connect(db_file)
        self.conn.text_factory = str

        #create cursor
        cursor = self.conn.cursor()

        cursor.execute("SELECT count(*) AS cnt FROM sqlite_master WHERE type='table' AND name='history'")
        row = cursor.fetchone()
        if row[0] == 0:
            #create history table
            cursor.execute('''create table history(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session varchar(20), 
                cmd TEXT,
                pre_cmd TEXT,
                prefix TEXT,
                window_title TEXT,
                time date,
                interval INTEGER
                )''')

        #fetch history
        cursor.execute('select id,session,cmd,pre_cmd,prefix,window_title,time,interval from history')

        while True:
            historys = cursor.fetchmany(size=1000)
            if not historys or len(historys) == 0:
                break

            for his in historys:
                history = {'cmd':his[2],'pre_cmd':his[3],'prefix':his[4],'window_title':his[5],'time':his[6],'interval':his[7]}
                self.add_to_stat(history)
                self.total_count = self.total_count + 1

        #大于最大数量时，删除 DELETE_NUM 条，此时不同步更新 stat，没有大问题
        if self.total_count >= MAX_HIS_NUM:
            result = cursor.execute("delete from history where id in (select id  from history order by id limit {})".format(DELETE_NUM))
            self.total_count = self.total_count - result.rowcount
            self.conn.commit()
            #清理数据库
            cursor.execute("VACUUM")

        #获取所有公共命令
        cursor.execute("SELECT count(*) AS cnt FROM sqlite_master WHERE type='table' AND name='common_cmd'")
        row = cursor.fetchone()
        if row[0] == 0:
            #create common_cmd table
            cursor.execute('''create table common_cmd(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cmd TEXT,
                back_size INTEGER,
                count INTEGER,
                time date
                )''')

        #fetch common_cmd
        cursor.execute('select id,cmd,back_size,count,time from common_cmd order by id desc')
        common_cmds = cursor.fetchmany(size=1000)

        self.all_common_cmds = []
        all_size = len(common_cmds)
        if common_cmds and all_size > 0:
            group_common_cmds = {}
            index = 0
            to_delete_common_cmds = []

            for common_cmd in common_cmds:
                index = index + 1
                # 删除400之后的，只保留400条，等下次大于500条的时候再次删除
                if index >= 400 and all_size > 500:
                    to_delete_common_cmds.append(common_cmd[0]) 
                else:
                    cmd_str = common_cmd[1]
                    group_common_cmds.setdefault(cmd_str, {'cmd':cmd_str, 'back_size':common_cmd[2],'count':0,'time':common_cmd[4]})
                    group_common_cmds[cmd_str]["count"] = group_common_cmds[cmd_str]["count"] + 1
                    group_common_cmds[cmd_str]["time"] = common_cmd[4]

            if len(to_delete_common_cmds) > 0:
                # create cursor and delete common_cmds
                cursor = conn.cursor()
                cursor.executemany("delete from common_cmd where cmd = id", to_delete_common_cmds)
                conn.commit()

            self.all_common_cmds = group_common_cmds.values()

    def add_to_stat(self,history):

        cmd = history["cmd"]
        title = history["window_title"]
        last_time = history["time"]
        prefix = history["prefix"]
        pre_cmd = history["pre_cmd"]
        l_interval = get_interval_level(history["interval"])

        self.history_stat.setdefault(cmd,{"count":0,"titles":{},"prefixs":{},"pre_cmds":{}})
        self.history_stat[cmd]["count"] = self.history_stat[cmd]["count"] + 1
        self.history_stat[cmd]["titles"].setdefault(title,0)
        self.history_stat[cmd]["titles"][title] = self.history_stat[cmd]["titles"][title] + 1
        self.history_stat[cmd]["prefixs"].setdefault(prefix,0)
        self.history_stat[cmd]["prefixs"][prefix] = self.history_stat[cmd]["prefixs"][prefix] + 1

        #pre_cmds的结构： "pre_cmds": { "pwd": { "1": 1 }, "java -jar arthas-boot.jar": { "4": 1 } }
        self.history_stat[cmd]["pre_cmds"].setdefault(pre_cmd,{})
        self.history_stat[cmd]["pre_cmds"][pre_cmd].setdefault(l_interval,0)
        self.history_stat[cmd]["pre_cmds"][pre_cmd][l_interval] = self.history_stat[cmd]["pre_cmds"][pre_cmd][l_interval] + 1
        
        self.history_stat[cmd]["last_time"] = last_time

        #当大于最大STAT数量时
        if len(self.history_stat) >= MAX_STAT_NUM:
            all_stats = []
            for cmd, stat in self.history_stat.items():
                all_stats.append({"cmd":cmd,"count":stat["count"],"last_time":stat["last_time"]})

            #排序后取前100,删除前100条数据
            sort_stats = sorted(all_stats, key=lambda stat : (stat['count'], stat['last_time']))
            to_delete_cmds = []
            for stat in sort_stats:
                to_delete_cmds.append((stat["cmd"],))
                if len(to_delete_cmds) >= DELETE_STAT_NUM:
                    break

            #create cursor
            print(tuple(to_delete_cmds))
            cursor = self.conn.cursor()
            cursor.executemany("delete from history where cmd = ?", to_delete_cmds)
            self.conn.commit()
            #清理数据库
            cursor.execute("VACUUM")
            #重新查询总数
            cursor.execute("select count(*) from history")
            count_result = cursor.fetchone()
            self.total_count = count_result[0]

            #从history_stat删除
            for del_cmd_tuple in to_delete_cmds:
                del self.history_stat[del_cmd_tuple[0]]

    def append_to_histable(self):
        if len(self.new_history) == 0 :
            return

        log_debug("write to history:" + str(self.new_history))

        his_list = []
        for his in self.new_history:
            his_list.append((his["session"],his["cmd"].encode('utf-8'),
                his["pre_cmd"].encode('utf-8'),his["prefix"].encode('utf-8'),
                his.get("window_title",'').encode('utf-8'),
                his["time"],his["interval"])) 

        #create cursor
        cursor = self.conn.cursor()
        cursor.executemany('''INSERT INTO history(session,cmd,pre_cmd,prefix,window_title,time,interval) 
                VALUES(?,?,?,?,?,?,?)''', his_list)
        self.conn.commit()

        #大于最大数量时，删除 DELETE_NUM 条，此时不同步更新 stat，没有大问题
        if self.total_count >= MAX_HIS_NUM:
            result = cursor.execute("delete from history where id in (select id  from history order by id limit {})".format(DELETE_NUM))
            self.total_count = self.total_count - result.rowcount
            self.conn.commit()
            #清理数据库
            cursor.execute("VACUUM")

        self.new_history = []
        self.last_append_time = nowTime()

    def add_history(self, history):
        #长度小于2的直接不记录了
        if len(history["cmd"]) <= 2:
            return

        if self.last_history["cmd"] == history["cmd"]:
            return
        self.last_history = history

        self.total_count = self.total_count + 1
        self.new_history.append(history)
        self.add_to_stat(history)

        now = nowTime()
        if len(self.new_history) >= 5 or self.last_append_time - now > 120 * 1000:
            self.append_to_histable()

    # 记录公共命令,先更新self.all_common_cmds，然后插入
    def append_common_cmd(self,common_cmd, back_size):
        exist_before = False

        for _common in self.all_common_cmds:
            if _common["cmd"] == common_cmd:
                _common["count"] = _common["count"] + 1
                _common["time"] = nowTime()
                
                back_size = _common["back_size"]   # 用于插入记录
                exist_before = True
                break

        # 应该不会有 back_size 还是None的
        if back_size is None:
            log_info("exception...")
            return

        if not exist_before:
            self.all_common_cmds.append( {'cmd':common_cmd,'back_size':back_size,'count':1,'time':nowTime()} )

        log_debug("add common_cmd:" + common_cmd)
        # 添加一条新的 common历史记录
        cursor = self.conn.cursor()
        cursor.execute('''INSERT INTO common_cmd(cmd,back_size,count,time) 
                VALUES(?,?,?,?)''', (common_cmd,back_size,1,nowTime()))
        self.conn.commit()

    # 获取最少使用的命令 count在5以下的  使用最少的一般可能是错误的，需要删除的
    def get_lfu_cmds(self,input=''):
        lfu_cmds = []
        for cmd, stat in self.history_stat.items():
            if stat["count"] < 5 and input in cmd:
                lfu_cmds.append({"command":cmd,"count":stat["count"],"last_time":stat["last_time"]})

        sorted_lfu_cmds = sorted(lfu_cmds, key=lambda item : (item['count'], item['last_time']))
        if len(sorted_lfu_cmds) > 10:
            sorted_lfu_cmds = sorted_lfu_cmds[:10]

        return sorted_lfu_cmds

    # 删除命令记录
    def delete_cmd(self, del_cmd):
        cursor = self.conn.cursor()
        cursor.execute("delete from history where cmd = ?", (del_cmd,))
        self.conn.commit()
        del self.history_stat[del_cmd]


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
        label.set_markup('<span foreground="blue">' + cgi.escape(data[:start1]) + '</span>'\
              + cgi.escape(data[start1:end1]) + '<span foreground="blue">' + \
              cgi.escape(data[end1:start2]) +'</span>'+ \
              cgi.escape(data[start2:]))  
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

class Tip(Gtk.Window):

    def __init__(self):

        self.recorder = {}
        self.terminal = None

        self.style_context = Gtk.StyleContext()
        self.provider = Gtk.CssProvider()

        self.provider.load_from_data("""
            #listbox {
                font-weight: 500;
            }
            """
        )

        self.init_tip_window()

    def init_tip_window(self):

        tip_window = Gtk.Window (Gtk.WindowType.POPUP)
        tip_window.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        #tip_window.set_transient_for(self.parent)
        self.style_context.add_provider_for_screen(tip_window.get_screen(), self.provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION )
        
        #listBox start
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

            #为了兼容zsh-autosuggestions这种，主动发一下 相当于 ↓ 这个按钮的控制码来消除zsh的自动提示
            if self.recorder[self.terminal].get("shell_auto_suggestion", False):
                down_control_seq = "\033[B"
                self.terminal.feed_child(down_control_seq,len(down_control_seq)) 

            if back_len > 0:
                back = "\033[D"
                self.terminal.feed_child(back * back_len, 3 * back_len)             
                self.terminal.feed_child(feed_cmd[start1:end1], len(feed_cmd[start1:end1]))
                forward = "\033[C"            
                self.terminal.feed_child(forward * back_len, 3 * back_len)
                        
            self.terminal.feed_child(feed_cmd[start2:], len(feed_cmd[start2:]))

            if pattern is not None :
                if back_size > 0:
                    back = "\033[D"
                    self.terminal.feed_child(back * back_size, 3 * back_size)
                self.recorder[self.terminal]["pattern"] = pattern 

            self.recorder[self.terminal]["selected_suggestion"] = row.data
            self.hide_suggestion_list()

        listbox.connect('row-activated', on_row_activated)
        #listBox end
        self.listbox = listbox

        tip_window.add(listbox)
        tip_window.connect("key-press-event", self.tip_key_press)

        self.tip_window = tip_window
        self.tip_create_time = nowTime()
        self.wait_unselect = False
        self.wait_autoclose = False
        
    def start_record(self, terminal):
        log_debug("liangyong record")

        # start
        (col, row) = terminal.get_cursor_position()
        self.recorder[terminal] = {"handler_id":0, "min_col": 99999, "row":row,
                                   "line_start_row":row,"pre_cmd":"","session": str(uuid.uuid1()),
                                   "hidden":False}
        
        # 添加contents-changed事件的处理
        log_debug("liangyong record contents-changed")
        self.recorder[terminal]["handler_id"] = terminal.connect('contents-changed', self.record_history)
        log_debug("liangyong record contents-changed childexited")
        self.recorder[terminal]["exit_id"] = terminal.connect('child-exited', self.exit_record)

    def record_history(self, terminal):
        log_debug("contents-changed")
        (col, row) = terminal.get_cursor_position()
        log_debug(str(col) + ":" + str(row))
        if col == 0 and row == 0 :
            log_debug("this is the terminal start, return")
            return

        last_saved_row = self.recorder[terminal]["row"]
        last_commited = self.recorder[terminal].get("commit_char",'')
        last_min_col = self.recorder[terminal].get("min_col",9999)
        line_wrap = False  #此次内容变化是否是长度导致的换行
        auto_show = False
        if row != last_saved_row:
            log_debug("row changed")
            log_debug(str(row) + ":" + str(last_saved_row))

            last_row_content = self.recorder[terminal].get("row_content","")
            last_cmd = self.recorder[terminal].get("cmd_content","")
            last_title = self.recorder[terminal].get("window_title","")
            last_min_col = self.recorder[terminal]["min_col"]  # 光标最小位置
            log_debug(last_min_col)
            
            init_content = self.recorder[terminal].get("init_content","")
            
            #检查这次的内容变化是否是长度导致的换行/或者长度导致的行数减小
            #需要判断row大于上一次的row，有过操作，且在三行内，才判断是不是换行，因为vi退出后也可能导致这种变化
            if row > last_saved_row and row - last_saved_row < 3 and self.recorder[terminal].get("has_operation",False):
                content_between_row =  self.get_text_content_with_last(terminal,last_saved_row, 0, row, terminal.get_column_count())
                log_debug(content_between_row)
                if content_between_row != '' and "\n" not in content_between_row:
                    line_wrap = True
            elif last_saved_row - row == 1:  #本来跨行，后退后不跨了？
                line_wrap = True

            log_debug(line_wrap)
            if not line_wrap: # 需要记录最后一条命令,并初始化新行
                # get output
                invi = self.recorder[terminal].get("invi",False)
                last_output = ''
                begin_row = last_saved_row + 1
                if row -1 >= begin_row and not invi: # vi模式中的不记录
                    if row-21 > begin_row:
                        begin_row = row-21
                    last_output,_ = self.get_text_content(terminal, begin_row, 0, row-1, terminal.get_column_count())
                    
                # last_output 可以用来提示
                log_debug(last_output)
    
                log_debug("print last content")
                log_debug(last_row_content)
                log_debug(init_content)
                log_debug(last_title)
                log_debug(last_cmd)
                log_debug(last_min_col)
                log_debug(last_commited)
    
                #记录上次的命令行输入
                last_cmd = last_cmd.strip()
                line_start_row = self.recorder[terminal]["line_start_row"]
                invi = self.recorder[terminal].get("invi",False)

                if (last_min_col > 0 or line_start_row != last_saved_row) and not invi and init_content != last_row_content :
                    if start_blank.match(last_row_content):
                        log_debug("not record because row_content :" + last_row_content)
                    elif last_cmd != '' and len(last_cmd) > 1 and not last_cmd in exclude_cmds :
                        last_cmd = self.special_handle(last_cmd)
                        pre_cmd = self.recorder[terminal]["pre_cmd"]
                        index = last_row_content.find(last_cmd)
                        prefix = last_row_content[0:index].strip()
                        session = self.recorder[terminal]["session"]
                        #记录2个命令之间的时间间隔
                        now = nowTime()
                        pre_time = self.recorder[terminal].get("pre_time",0)
                        interval = now - pre_time
                        if interval >= 600000 :
                            interval = -1

                        history = {"time":now,"prefix":prefix,"cmd":last_cmd,"window_title":last_title,"pre_cmd": pre_cmd,"session":session,"interval":interval}
                        log_debug(history)
                        his_recorder.add_history(history)
                        self.recorder[terminal]["pre_cmd"] = last_cmd
                        self.recorder[terminal]["pre_time"] = now

                        # 尝试提取公共部分
                        select_cmd = self.recorder[terminal].get("selected_suggestion",'')
                        select_pattern = self.recorder[terminal].get("pattern",None)
                        if select_cmd != '' and select_cmd != last_cmd and select_pattern is None:
                            log_debug("need try to get common")
                            common_cmd, back_size = get_common_cmd(last_cmd, select_cmd)
                            # common_cmd 为空表示没有公共部分
                            if common_cmd != '':
                                his_recorder.append_common_cmd(common_cmd,back_size)
                        elif select_pattern is not None and select_pattern.match(last_cmd):
                            his_recorder.append_common_cmd(select_cmd, None)

                        self.recorder[terminal]["selected_suggestion"] = ''
                        self.recorder[terminal]["pattern"] = None

                    else:
                        log_debug("not record because of last_cmd:" + last_cmd)
    
                # 初始化新行的相关变量
                self.init_new_row(terminal,col,row)
                auto_show = True

        self.recorder[terminal]["row"] = row
        if (last_min_col > col and self.recorder[terminal]["line_start_row"] == row):
            self.recorder[terminal]["min_col"] = col
        #或者一开始是0，未进行过操作自动变到当前位置：对应场景是有时反应慢，过一会才正常显示在屏幕上
        elif last_min_col == 0 and not self.recorder[terminal]["has_operation"] :
            self.recorder[terminal]["min_col"] = col
            auto_show=True

        min_col =  self.recorder[terminal]["min_col"]   
        line_start_row = self.recorder[terminal]["line_start_row"]

        log_debug(last_commited)
        log_debug((line_start_row,row))
        log_debug(self.recorder[terminal].get("invi",False))

        # 为了修复跨行问题，多获取一行 这样也只能解决命令跨一行的问题，先这样，后面再看
        # 这里修复的场景是：变为第二行的首位，反应慢还没变到正常位置时
        row_content,_ = self.get_text_content(terminal,line_start_row, 0, row+1, terminal.get_column_count())
        lf_index = row_content.find("\n")
        if lf_index != -1:
            row_content = row_content[:lf_index]

        # 必须再次调用，不能直接由row_content得来,否则前面的PS1如zsh的含有特殊字符时，会出错
        cmd_content,cmd_attrs = self.get_text_content(terminal,line_start_row, min_col, row+1, terminal.get_column_count())
        lf_index = cmd_content.find("\n")
        if lf_index != -1:
            cmd_content,cmd_attrs = cmd_content[:lf_index],cmd_attrs[:lf_index]
        # 对于如redis-cli,zsh这样的，后面可能会自动出现提示信息，这里尝试舍弃后面的部分
        cmd_content = self.check_shell_auto_suggestion(terminal,cmd_content,cmd_attrs)

        self.recorder[terminal]["row_content"] = row_content
        self.recorder[terminal]["cmd_content"] = cmd_content
        self.recorder[terminal]["window_title"] = self.get_window_title(terminal)

        # 以下逻辑决定是否展示提示
        # 如果在单词级别提示下，则展示单词级别提示
        if terminal.is_focus():
            # 未跨行且最后输入的时单个字符并且不在vi下则提示
            if last_commited != '' and line_start_row == row and \
             not self.recorder[terminal].get("invi",False) and \
             not self.recorder[terminal]["hidden"]:
                self.show_tip_window(terminal)
            elif auto_show and min_col > 0:
                self.auto_suggestion(terminal)
            else:
                self.hide_suggestion_list()

    def check_shell_auto_suggestion(self,terminal,cmd_content,cmd_attrs):
        if len(cmd_attrs) > 0 and len(cmd_content) == len(cmd_attrs):
            first_fore = cmd_attrs[0].fore
            index = 0
            #log_debug((first_fore.blue,first_fore.green,first_fore.red))
            for attr in cmd_attrs:
                cur_for = attr.fore
                #log_debug((cur_for.blue,cur_for.green,cur_for.red,attr.column))
                #根据颜色对比，如redis-cli,zsh这样使用VT100控制码浅色提示的，提示信息的颜色与前面的输入是不一样的
                if cur_for.blue != first_fore.blue or cur_for.green != first_fore.green or cur_for.red != first_fore.red:
                    #log_debug("color check break")
                    break
                index += 1

            if index < len(cmd_content):
                log_debug("may be have auto prompt string, such as redis-cli,zsh")
                self.recorder[terminal]["shell_auto_suggestion"] = True
                cmd_content = cmd_content[:index]
            else:
                self.recorder[terminal]["shell_auto_suggestion"] = False
        return cmd_content

    def init_new_row(self,terminal,col,row):
        log_debug("init_new_row")
        new_row_content,_ = self.get_text_content(terminal,row, 0, row, terminal.get_column_count())
        self.recorder[terminal]["min_col"] = col
        self.recorder[terminal]["commit_char"] = ''
        self.recorder[terminal]["has_operation"] = False   # if had operation(eg: move) on current line
        self.recorder[terminal]["init_content"] = new_row_content
        self.recorder[terminal]["row_content"] = new_row_content
        self.recorder[terminal]["line_start_row"] = row
        self.recorder[terminal]["shell_auto_suggestion"] = False  #
        self.recorder[terminal]["selected_suggestion"] = ''
        self.recorder[terminal]["pattern"] = None
        self.recorder[terminal]["hidden"] = False
        self.check_if_invi(terminal)

    def check_if_invi(self,terminal):
        #尝试检查是否在vi编辑中,不一定准确，但是没找到更好的办法
        adj = terminal.get_vadjustment()
        lower,upper,value,p_size = adj.get_lower(), adj.get_upper(), adj.get_value(),adj.get_page_size()
        #log_debug(str(lower) +"/" + str(upper) +"/" + str(value) + "/" + str(p_size))
        #log_debug(terminal.get_row_count())
        if lower != 0 and lower == value and p_size == terminal.get_row_count():
            log_debug("now in vi")
            self.recorder[terminal]["invi"] = True
        else:
            self.recorder[terminal]["invi"] = False

    # Don't get the last char if is '\n'    
    def get_text_content(self,terminal,start_row,start_col,end_row,end_col ):
        content_attr = terminal.get_text_range(start_row, start_col, end_row, end_col, lambda *a: True)
        content = content_attr[0]
        attrs = content_attr[1]
        if content.endswith("\n"):
            content = content[:-1]
            attrs = attrs[:-1]
        return content,attrs

    def get_text_content_with_last(self,terminal,start_row,start_col,end_row,end_col ):
        content_attr = terminal.get_text_range(start_row, start_col, end_row, end_col, lambda *a: True)
        content = content_attr[0]
        return content

    # title有可能取空
    def get_window_title(self,terminal):
        title = terminal.get_window_title()
        if title is None:
            title = ''
        return title

    #当前仅对cd 进行特殊处理，统一去掉最后的/
    def special_handle(self, last_cmd):
        if last_cmd.startswith("cd ") and last_cmd.endswith("/"):
            last_cmd = last_cmd.rstrip("/")

        #普通的命令，中间有多个空格的，替换为1个
        if not '"' in last_cmd and not "'" in last_cmd:
            last_cmd = " ".join(last_cmd.split())

        return last_cmd

    def exit_record(self, terminal, status):
        log_debug("exit_record")
        his_recorder.append_to_histable()
        if self.recorder.has_key(terminal):
            if self.recorder[terminal].get("handler_id",0) != 0:
                terminal.disconnect(self.recorder[terminal]["handler_id"])
            del(self.recorder[terminal])
            
        #如果所有窗口都关闭了，则关闭连接
        if len(self.recorder) == 0:
            try:
                his_recorder.conn.close()
            except:
                log_debug("close slite exception")
            else:
                log_debug("close sqlite success")

    #上一个命令完成后，自动提示下一个命令并选中第一个
    def auto_suggestion(self,terminal):
        log_debug("auto_suggestion")

        # 将要添加的提示
        list_add = []

        pre_cmd = self.recorder[terminal]["pre_cmd"]
        row_content = self.recorder[terminal]["row_content"]
        min_col = self.recorder[terminal]["min_col"] 
        prefix = row_content[0:min_col].strip()

        log_debug(prefix)
        log_debug(pre_cmd)

        min_interval = 2   #间隔小于15秒的

        for cmd, stat in his_recorder.history_stat.items():
            #当长度小于2时，不需要
            if len(cmd) <= 2:
                continue

            #根据 pre_cmd prefix 两者来判断
            prefixs = stat["prefixs"]
            pre_cmds = stat["pre_cmds"]
            titles = stat["titles"]

            prefix_count = 0
            for _prefix,count in prefixs.items():
                if _prefix == prefix:
                    prefix_count = count                    

            #如果等于0，则没必要继续了
            if prefix_count ==0:
                continue

            # 自动提示是在上一个命令执行后，自动展示 所以有必要判断历史命令间隔，小于一定时间间隔的才有意义
            precmd_count = 0

            # pre_cmds 转化为 {"cmd":total_count} 的结构 再去匹配
            to_match_precmds = {}
            for precmd, intervals in pre_cmds.items():
                _precmd_count = 0
                _valid_count = 0
                for interval,_count in intervals.items():
                    # 间隔没有意义的不管了 interval的值参见 get_interval_level
                    if interval >= 10:
                        continue

                    _precmd_count = _precmd_count + _count
                    if interval <= min_interval:
                        _valid_count = _valid_count + _count

                to_match_precmds[precmd] = {"count":_precmd_count, "valid_count": _valid_count}

            for _precmd, item in to_match_precmds.items():
                if _precmd == pre_cmd:
                    precmd_count = item["valid_count"]

            if precmd_count == 0:
                continue

            if prefix_count > 0 and precmd_count > 0:
                log_debug("add:" + cmd)
                prefix_score = float(prefix_count)/(2 + prefix_count)
                precmd_score = float(precmd_count)/(2 + precmd_count) 
                list_add.append({"cmd":cmd,"prefix_count": prefix_count,
                    "precmd_count":precmd_count, "score":prefix_score+precmd_score,
                    "start":0,"end":0})

        #根据总得分排序，最多取前四个
        list_add.sort(key=by_score,reverse = True)
        if len(list_add) > 4:
            list_add = list_add[:4]

        if len(list_add) == 0:
            log_debug("return")
            return

        #如果与上一条命令相同的命令排在了首位则移动到末尾 由于自动提示不精确貌似经常出现这种情况
        first_item =  list_add[0]
        if first_item["cmd"] == pre_cmd:
            list_add.pop(0)
            list_add.append(first_item)

        log_debug(list_add)

        # destroy and rebuild window
        self.tip_window.destroy()
        self.init_tip_window()
        self.wait_autoclose = True
        self.tip_window.override_font(terminal.get_font().copy())

        for suggest_cmd in list_add:
            self.listbox.add(ListBoxRowWithData(suggest_cmd["cmd"],0,0,0,0))

        height = terminal.get_char_height() #字符高度
        #计算光标绝对位置
        screen_x, screen_y = self.get_screen_cursor_postition(terminal)
        #提示框应该出现的位置 光标在屏幕上的x, 减去 ListBoxRow 的 margin
        showx,showy = screen_x - 5, screen_y + height

        #显示提示框
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.tip_window.move(showx,showy)
        self.tip_window.show_all()

        # 计算提示框出现的位置，如果超出了屏幕则显示在上方
        self.ensure_tip_inscreen(terminal,showx,showy)
        self.terminal = terminal

        #设置选中第一个
        self.listbox.select_row(self.listbox.get_row_at_index(0))
        self.listbox.get_row_at_index(0).grab_focus()

        #定时3.5秒后自动关闭提示框
        GLib.timeout_add(AUTO_TIP_WAIT, self.auto_close_tip, None)

    def auto_close_tip(self, data):
        now = nowTime()
        #存在操作很快，重复GLib.timeout_add_seconds的情况，所以判断下时间
        if (now - self.tip_create_time >= AUTO_TIP_WAIT - 500) and self.wait_autoclose:
            self.hide_suggestion_list()
            self.wait_autoclose = False

        return False   #结束定时
        
    def ensure_tip_inscreen(self,terminal,showx,showy):
        # 计算提示框出现的位置，如果超出了屏幕则显示在上方
        show_at_above = False
        screen_h = Gdk.Screen.height()
        _tip_w, tip_h = self.tip_window.get_size()
        if showy + tip_h > screen_h:
            show_at_above = True
            showy = showy - tip_h - terminal.get_char_height()

        if show_at_above:
            self.tip_window.move(showx,showy)
            self.tip_window.set_opacity(0.8)  #设置透明度
        else:
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
        
        #当输入长度大于等于2,小于等于20时，才进行模糊匹配
        if input_len < 2 or input_len > 20:
            return -1, -1, -1, -1
        
        matchObj = cur_pattern.search(his_cmd) 
        if matchObj:
            match_region = matchObj.span(3)
            in_word = matchObj.group(2)
            #log_debug(in_word)
            # 如果 匹配到的部分在一个单词中，单词前后其他部分小于等于20才认为是匹配，用于过滤 模糊匹配到 git 的 commitid等情况
            if len(in_word) - input_len <= 20:
                return len(cur_args),len(cur_cmd), match_region[0], match_region[1]

        return -1, -1, -1, -1

    def reshow_suggestion_list(self,terminal,showx,showy,cur_input):
        log_debug("reshow_suggestion_list:"+ cur_input)

        # 销毁后重建提示框
        self.tip_window.destroy()
        self.init_tip_window()
        self.tip_window.override_font(terminal.get_font().copy())

        select_pattern = self.recorder[terminal].get("pattern",None)
        if select_pattern is not None and select_pattern.match(cur_input):
            log_info("in common cmd mode")
            return
        else:
            self.recorder[terminal]["pattern"] = None

        # 将要添加的提示
        list_add = []
        match_len = len(cur_input)
        #如果时不带空格的情况下 x是-1，则args是全部input
        x = cur_input.find(' ')
        cur_cmd = cur_input[:x+1]
        cur_args = cur_input[x+1:]
        cur_pattern = re.compile(r'^'+ re.escape(cur_cmd) + r'(.*\W)?(\w*('+ re.escape(cur_args) + r')\w*)((\W.*)|$)', re.U)

        #获取当前的 title pre_cmd prefix 用于计算
        title = self.get_window_title(terminal)
        pre_cmd = self.recorder[terminal]["pre_cmd"]

        row_content = self.recorder[terminal]["row_content"]
        min_col = self.recorder[terminal]["min_col"] 
        prefix = row_content[0:min_col].strip()

        # 缓存此次计算出的结果
        key = "--".join((title , pre_cmd , prefix))
        self.recorder[terminal].setdefault("cache",{})
        if self.recorder[terminal]["cache"].get("__key__",) != key:
            self.recorder[terminal]["cache"] = {}
            self.recorder[terminal]["cache"]["__key__"] = key

        cur_is_hiscmd = False
        for cmd, stat in his_recorder.history_stat.items():
            if cmd == cur_input:
                cur_is_hiscmd = True
                continue

            back_len,start1,end1,start2  = self.find_match(cur_input,cur_cmd,cur_args,cur_pattern,cmd)
            if back_len >=0 and len(cmd) > 3:
                self.calculate_and_add(terminal, title, pre_cmd, prefix, cmd, stat, back_len, start1, end1, start2, list_add)

        #根据总得分排序，最多取前十个
        list_add.sort(key=by_score,reverse = True)
        max_num = int(round(SUGGESTION_NUM * 1.5))
        if len(list_add) > max_num:
            list_add = list_add[:max_num]

        self.process_common(cur_input,list_add)

        #根据总得分排序，最多取前十个
        list_add.sort(key=by_score,reverse = True)
        if len(list_add) > SUGGESTION_NUM:
            list_add = list_add[:SUGGESTION_NUM]

        log_debug(list_add)
        for suggest_cmd in list_add:
            # 这里 back_len 是用于模糊匹配时回退多少个字符才开始输入第一段的
            # back_size 是表示 如果是公共命令，输入进去后需要回退多少个字符来输入其他部分
            self.listbox.add( ListBoxRowWithData(suggest_cmd["cmd"],suggest_cmd["back_len"],
                                            suggest_cmd["start1"],suggest_cmd["end1"],
                                            suggest_cmd["start2"],suggest_cmd.get("pattern",None)
                                            ,suggest_cmd.get("back_size",0))
                            )
        
        if len(self.listbox.get_children()) == 0:
            log_debug("return")
            return

        # 如果当前输入不是一个历史命令,且最后不是一个空格就自动选中第一个
        if AUTO_SELECT_ENABLE and not cur_is_hiscmd and cur_input[-1:] != ' ' and cur_input not in exclude_cmds:
            # 自动选中第一个 1秒后取消自动选中
            self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
            self.listbox.select_row(self.listbox.get_row_at_index(0))
            self.listbox.get_row_at_index(0).grab_focus()
            self.wait_unselect = True
            #定时1秒后自动取消第一条的选中
            #GLib.timeout_add(AUTO_SELECT_WAIT, self.auto_unselect_first, None)
        else:
            self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)

        self.tip_window.move(showx,showy)
        self.tip_window.show_all()

        # 计算提示框出现的位置，如果超出了屏幕则显示在上方
        self.ensure_tip_inscreen(terminal,showx,showy)
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.terminal = terminal

    def auto_unselect_first(self,data):
        log_debug("auto_unselect_first")
        now = nowTime()
        #存在操作很快，重复GLib.timeout_add_seconds的情况，所以判断下时间
        if (now - self.tip_create_time >= AUTO_SELECT_WAIT - 100) and self.wait_unselect:
            # 自动取消第一条的选中
            self.listbox.unselect_all()
            self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
            #self.tip_window.hide()
            self.tip_window.show_all()
            self.wait_unselect = False
            self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)

        return False   #结束定时

    #计算得分 cmd是历史命令，stat是统计数据 back_len是需要回退的长度，end1是第一段输入结束的索引 start2是第二段输入开始的索引 list_add是加入的提示列表
    def calculate_and_add(self, terminal, title, pre_cmd, prefix, cmd, stat, back_len, start1, end1, start2, list_add):

        #如果已缓存，则尝试取缓存
        cache_result = self.recorder[terminal]["cache"].get(cmd, None)
        if cache_result is not None:
            log_debug("from cache")
            cache_result.update({"back_len":back_len, "start1":start1, "end1": end1,"start2":start2 })
            list_add.append(cache_result)
            return

        score = 0
        count = stat["count"]
        last_time = stat["last_time"]  # not used now
                
        # 计算分数  使用 x/(base+x)
        title_match, title_count, title_relation = max_match_str(title,stat["titles"])

        title_score = title_match * (float(title_count)/(1 + title_count)) 

        # pre_cmds转化为 {"cmd":total_count} 的结构 再去匹配
        to_match_precmds = {}
        for precmd, intervals in stat["pre_cmds"].items():
            precmd_count = 0
            for interval,_count in intervals.items():
                # 间隔没有意义的不管了 interval的值参见 get_interval_level
                if interval >= 10:
                    continue
                precmd_count = precmd_count + _count

            to_match_precmds[precmd] = precmd_count

        pre_cmd_match, pre_cmd_count, pre_cmd_relation  = max_match_str(pre_cmd, to_match_precmds, 2)

        precmd_score = pre_cmd_match * (float(pre_cmd_count)/(1 + pre_cmd_count))


        prefix_match, prefix_count, prefix_relation  = max_match_str(prefix, stat["prefixs"], 1)

        prefix_score = prefix_match * (float(prefix_count)/(3 + prefix_count)) 

        phase1_score = 3 * (title_score * title_relation + precmd_score * pre_cmd_relation + prefix_score * prefix_relation)\
            /(title_relation + pre_cmd_relation + prefix_relation)

        now = nowTime()
        period = now - last_time   #毫秒
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
        score = phase1_score  - period_score

        if DEBUG_ENABLE:
            append_result = {"cmd":cmd,"phase1_score":phase1_score, "score":score,
            "title_match":title_match, "title_count":title_count, "title_score":title_score,
            "pre_cmd_match":pre_cmd_match, "pre_cmd_count":pre_cmd_count, "precmd_score":precmd_score,
            "prefix_match":prefix_match, "prefix_count":prefix_count, "prefix_score":prefix_score,
            "period_score":period_score, "count": count, "count_score":count_score,
            "back_len":back_len, "start1":start1, "end1": end1,"start2":start2}
        else:
            append_result = {"cmd":cmd,"phase1_score":phase1_score,"count": count,
                "period_score": period_score, "score":score, "back_len":back_len,
                "start1":start1, "end1": end1,"start2":start2}

        list_add.append(append_result)
        #设置缓存
        self.recorder[terminal]["cache"][cmd] = append_result
    
    # 处理当前的list_add是否有公共命令
    def process_common(self, cur_input, list_add):
        log_debug("process_common")
        #如果时不带空格的情况下 x是-1，则args是全部input
        x = cur_input.find(' ')
        cur_cmd = cur_input[:x+1]
        cur_args = cur_input[x+1:]
        cur_pattern = re.compile(r'^'+ re.escape(cur_cmd) + r'(.*\W)?(\w*('+ re.escape(cur_args) + r')\w*)((\W.*)|$)')

        matched_commons = []
        for common_cmd in his_recorder.all_common_cmds:
            back_len,start1,end1,start2  = self.find_match(cur_input,cur_cmd,cur_args,cur_pattern,common_cmd["cmd"])
            if back_len < 0 or common_cmd["cmd"] == cur_input:
                continue

            # 给公共命令组装 编译后的 正则
            common_pattern = common_cmd.get("pattern",None)
            if common_pattern is None:
                common_str = common_cmd["cmd"]
                back_size = common_cmd["back_size"]
                index = len(common_str) - back_size
                common_pattern = re.compile(r'' + common_str[:index] + r'[^ ]*' + common_str[index:])
                common_cmd["pattern"] = common_pattern

            item = common_cmd.copy()
            item.update({"back_len":back_len,"start1":start1,"end1":end1,"start2":start2})
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
                common_pattern  = item["pattern"]
                _common_cmd_str = item["cmd"]

                if common_pattern.match(_cmd_str):
                    phase1_score = suggest_cmd["phase1_score"]
                    item["phase1_score"] = item.get("phase1_score",0) + phase1_score
                    item["m_count"] = item.get("m_count",0) + 1     #计算匹配此公共的suggest个数，用于最后计算平均值
                    item["_count_sum"] = item.get("_count_sum",0) + _count   #用于最后计算count平均值
                    final_commons[_common_cmd_str] = item
                    # 不break的原因是比如 有 ps -ef|grep xxx1 ps -ef|grep xxx2
                    # 公共部分可能提取到 ps -ef|grep 也有 ps -ef|grep xxx
                    # break的话就只能匹配到一个了
                    #break

        for _, common in final_commons.items():
            m_count = common["m_count"]
            average_count = float(common["_count_sum"] + common["count"])/m_count

            common["phase1_score"] = common["phase1_score"]/m_count
            count_score = float(average_count)/(4 + average_count)
            common["score"] = common["phase1_score"] + count_score
            common["common"] = True # 表示是公共部分

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
        is_shift_mask = (event.get_state() & Gdk.ModifierType.SHIFT_MASK == Gdk.ModifierType.SHIFT_MASK)

        #如果时shift+空格，则临时关闭此行的自动提示
        if is_shift_mask and key == 'space':
            log_debug("close tip_window")
            self.recorder[terminal]["hidden"] =  not self.recorder[terminal]["hidden"]
            self.hide_suggestion_list()
            return True

        if self.tip_window.is_visible():
            if self.listbox.get_selected_row() and (key == 'Down' or key == 'Up' or key == 'Return'):
                new_event = Gdk.Event.copy(event)         
                self.tip_window.emit("key-press-event",new_event)
                return True   # stop event handle by other handlers

            if not self.listbox.get_selected_row():
                if key == 'Down':
                    log_debug("active and set first row")             
                    self.listbox.select_row(self.listbox.get_row_at_index(0))
                    self.listbox.get_row_at_index(0).grab_focus()
                    return True    #stop event handle by other handlers  

                if key == 'Up':
                    log_debug("active and set last row")             
                    max_index = len(self.listbox.get_children()) -1
                    self.listbox.select_row(self.listbox.get_row_at_index(max_index))
                    self.listbox.get_row_at_index(max_index).grab_focus()
                    return True    #stop event handle by other handlers

    def tip_key_press(self, tip_window, event):
        log_debug("tip_key_press")
        # 只要提示框一有任何操作则 自动关闭和自动取消选中第一条设置为 False
        self.wait_unselect = False
        self.wait_autoclose = False
        key = Gdk.keyval_name(event.keyval)
        if key != 'Down' and key != 'Up' and key != 'Return':
            self.hide_suggestion_list()
            
            new_event = Gdk.Event.copy(event)         
            self.terminal.emit("key-press-event",new_event)

        max_index = len(self.listbox.get_children()) -1
        first_row = self.listbox.get_row_at_index(0)
        last_row = self.listbox.get_row_at_index(max_index)
        select_row = self.listbox.get_selected_row()

        if key == 'Down':
            if select_row == last_row:
                log_debug("active and set first row")             
                self.listbox.select_row(first_row)
                first_row.grab_focus()
                return True    #stop event handle by other handlers
        elif key == "Up":
            if select_row == first_row:
                log_debug("active and set last row")             
                self.listbox.select_row(last_row)
                last_row.grab_focus()
                return True    #stop event handle by other handlers

    #在commit事件并且content发生变化后展示提示
    def show_tip_window(self,terminal):
        col, row = terminal.get_cursor_position()

        min_col = self.recorder[terminal]["min_col"]
        current_cmd,_ = self.get_text_content(terminal,row, min_col, row, col-1)
        current_cmd = current_cmd.lstrip()
        log_debug(current_cmd)   #获取当前的输入用于提示

        #如果是空的，则不提示
        if current_cmd == '':
            return

        width = terminal.get_char_width()  #字符宽度
        height = terminal.get_char_height() #字符高度
        #计算提示框应该出现的位置
        screen_x, screen_y = self.get_screen_cursor_postition(terminal)
        #提示框应该出现的位置 光标在屏幕上的x, 减去 当前输入的宽度 再减去 ListBoxRow 的 margin
        showx,showy = screen_x - len(current_cmd) * width - 5, screen_y + height

        self.reshow_suggestion_list(terminal,showx,showy,current_cmd)
    
    #计算光标在屏幕上的绝对位置
    def get_screen_cursor_postition(self,terminal):
        #获取光标的行列
        (col, row) = terminal.get_cursor_position()

        adj = terminal.get_vadjustment()   #获取滚动条
        screen_row = row - adj.get_value() #row减去滚动条值，则是 terminal 可见区看见的行数
        width = terminal.get_char_width()  #字符宽度
        height = terminal.get_char_height() #字符高度
        x,y = col * width, screen_row * height   # terminal上光标所在位置

        x1,y1 = terminal.translate_coordinates(terminal.get_toplevel(),x,y)  # 光标在terminal的顶级窗口的位置

        gdk_p = Gdk.Window.get_origin(terminal.get_toplevel().get_window())  # terminal顶级窗口在屏幕上的位置
        #log_debug(gdk_p)
        return x1 + gdk_p.x, y1 + gdk_p.y

    # tip窗口展示的逻辑，首先commmit触发 然后content-change后展示提示框
    def terminal_commit(self,terminal,text,size):
        log_debug("terminal_commit:" + str(text) + ":" + str(size))

        accept = False
        if size == 1 and (32 <= ord(text[0]) <= 126):
            accept = True
            self.recorder[terminal]["commit_char"] = text[0]

        if not accept :
            self.recorder[terminal]["commit_char"] = ''
            if self.tip_window.is_visible():
                self.hide_suggestion_list()      
    #丢失焦点
    def focus_out(self,terminal,event):
        log_debug("focus_out")
        self.tip_window.hide()

    def start_for_terminal(self,terminal):
        terminal.connect("key-press-event", self.terminal_keypress)
        terminal.connect_after("commit", self.terminal_commit)
        terminal.connect("focus-out-event", self.focus_out)

        self.start_record(terminal)

    def add_view_menu(self, menu):
        item = Gtk.MenuItem.new_with_mnemonic(_('View _History'))
        item.connect("activate", self.open_his_view)
        menu.append(item)

    # 展示窗口用于删除部分错误命令 代码框架复制于 terminatorlib/plugins/custom_commands.py
    def open_his_view(self,widget,data=None):
        log_debug("show history view")
        #先把缓存的命令输入到历史
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
        
        cmd_list= his_recorder.get_lfu_cmds()
        for command in cmd_list:
          store.append([command['command'], command['count']])
    
        treeview = Gtk.TreeView(store)
        #treeview.connect("cursor-changed", self.on_cursor_changed, ui)
        selection = treeview.get_selection()
        selection.set_mode(Gtk.SelectionMode.SINGLE)
        selection.connect("changed", self.on_selection_changed, ui)
        ui['treeview'] = treeview
    
        renderer = Gtk.CellRendererText()
        column = Gtk.TreeViewColumn(_("Command"), renderer, text=CC_COL_COMMAND)
        column.set_fixed_width(420)
        column.set_max_width(420)
        column.set_expand(True)
        treeview.append_column(column)

        renderer = Gtk.CellRendererText()
        column = Gtk.TreeViewColumn(_("Count"), renderer, text=CC_COL_COUNT)
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
        del(self.dbox)
        dbox.destroy()
        return

    def on_selection_changed(self,selection, data=None):
        log_debug("on_selection_changed")
        treeview = selection.get_tree_view()
        (model, iter) = selection.get_selected()
        #log_debug(model.get_value(iter,0))
        data['button_delete'].set_sensitive(iter is not None)

    def on_search_changed(self,entry):
        log_debug("on_search_changed")
        input = entry.get_text()

        cmd_list= his_recorder.get_lfu_cmds(input)
        self.store.clear()

        for command in cmd_list:
            self.store.append([command['command'], command['count']])

    def on_delete(self,button,data):
        log_debug("on_delete")
        treeview = data['treeview']
        selection = treeview.get_selection()
        (store, iter) = selection.get_selected()
        if iter:
            del_cmd = store.get_value(iter,0)
            log_debug(del_cmd)
            # 删除相关 history 和 history_stat
            his_recorder.delete_cmd(del_cmd)
            store.remove(iter)
            