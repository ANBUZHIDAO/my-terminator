#!/usr/bin/env python3
# -*-coding: UTF-8 -*-

from utils import *

import os
import sys
import datetime
import time
import json
import re
import sqlite3
import uuid
import math
import html


def adapt_datetime(ts):
    return time.mktime(ts.timetuple())


sqlite3.register_adapter(datetime.datetime, adapt_datetime)

db_file = os.path.join(os.path.expanduser('~'), '.terminator.db')
# history记录数大于此数时，删除前 DELETE_NUM条。出现性能问题后可先把这个值调小，再想其他办法解决
MAX_HIS_NUM = 100000
DELETE_NUM = 10000             # 删除时删除前多少条
MAX_STAT_NUM = 3000            # cmd数量最大800，多出的删除。出现性能问题后可先把这个值调小，再想其他办法解决
DELETE_STAT_NUM = 400          # stat删除时删除前多少条


class History():
    def __init__(self):
        self.new_history = []
        self.last_history = {"cmd": ""}
        self.total_count = 0
        self.history_stat = {}
        self.init_history()
        self.last_append_time = nowTime()

    def init_history(self):
        self.conn = sqlite3.connect(db_file)
        self.conn.text_factory = str

        # create cursor
        cursor = self.conn.cursor()

        cursor.execute(
            "SELECT count(*) AS cnt FROM sqlite_master WHERE type='table' AND name='history'")
        row = cursor.fetchone()
        if row[0] == 0:
            # create history table
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

        # fetch history
        cursor.execute(
            'select id,session,cmd,pre_cmd,prefix,window_title,time,interval from history')

        while True:
            historys = cursor.fetchmany(size=1000)
            if not historys or len(historys) == 0:
                break

            for his in historys:
                history = {'cmd': his[2].decode(), 'pre_cmd': his[3].decode(), 'prefix': his[4].decode(
                ), 'window_title': his[5].decode(), 'time': his[6], 'interval': his[7]}

                log_debug(history)
                self.add_to_stat(history)
                self.total_count = self.total_count + 1

        # 大于最大数量时，删除 DELETE_NUM 条，此时不同步更新 stat，没有大问题
        if self.total_count >= MAX_HIS_NUM:
            result = cursor.execute(
                "delete from history where id in (select id  from history order by id limit {})".format(DELETE_NUM))
            self.total_count = self.total_count - result.rowcount
            self.conn.commit()
            # 清理数据库
            cursor.execute("VACUUM")

        # 获取所有公共命令
        cursor.execute(
            "SELECT count(*) AS cnt FROM sqlite_master WHERE type='table' AND name='common_cmd'")
        row = cursor.fetchone()
        if row[0] == 0:
            # create common_cmd table
            cursor.execute('''create table common_cmd(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cmd TEXT,
                back_size INTEGER,
                count INTEGER,
                time date
                )''')

        # fetch common_cmd
        cursor.execute(
            'select id,cmd,back_size,count,time from common_cmd order by id desc')
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
                    group_common_cmds.setdefault(cmd_str, {
                                                 'cmd': cmd_str, 'back_size': common_cmd[2], 'count': 0, 'time': common_cmd[4]})
                    group_common_cmds[cmd_str]["count"] = group_common_cmds[cmd_str]["count"] + 1
                    group_common_cmds[cmd_str]["time"] = common_cmd[4]

            if len(to_delete_common_cmds) > 0:
                # create cursor and delete common_cmds
                cursor = self.conn.cursor()
                cursor.executemany(
                    "delete from common_cmd where cmd = id", to_delete_common_cmds)
                self.conn.commit()

            self.all_common_cmds = group_common_cmds.values()

    def add_to_stat(self, history):

        cmd = history["cmd"]
        title = history["window_title"]
        last_time = history["time"]
        prefix = history["prefix"]
        pre_cmd = history["pre_cmd"]
        l_interval = get_interval_level(history["interval"])

        self.history_stat.setdefault(
            cmd, {"count": 0, "titles": {}, "prefixs": {}, "pre_cmds": {}})
        self.history_stat[cmd]["count"] = self.history_stat[cmd]["count"] + 1
        self.history_stat[cmd]["titles"].setdefault(title, 0)
        self.history_stat[cmd]["titles"][title] = self.history_stat[cmd]["titles"][title] + 1
        self.history_stat[cmd]["prefixs"].setdefault(prefix, 0)
        self.history_stat[cmd]["prefixs"][prefix] = self.history_stat[cmd]["prefixs"][prefix] + 1

        # pre_cmds的结构： "pre_cmds": { "pwd": { "1": 1 }, "java -jar arthas-boot.jar": { "4": 1 } }
        self.history_stat[cmd]["pre_cmds"].setdefault(pre_cmd, {})
        self.history_stat[cmd]["pre_cmds"][pre_cmd].setdefault(l_interval, 0)
        self.history_stat[cmd]["pre_cmds"][pre_cmd][l_interval] = self.history_stat[cmd]["pre_cmds"][pre_cmd][l_interval] + 1

        self.history_stat[cmd]["last_time"] = last_time

        # 当大于最大STAT数量时
        if len(self.history_stat) >= MAX_STAT_NUM:
            all_stats = []
            for cmd, stat in self.history_stat.items():
                all_stats.append(
                    {"cmd": cmd, "count": stat["count"], "last_time": stat["last_time"]})

            # 排序后取前100,删除前100条数据
            sort_stats = sorted(all_stats, key=lambda stat: (
                stat['count'], stat['last_time']))
            to_delete_cmds = []
            for stat in sort_stats:
                to_delete_cmds.append((stat["cmd"],))
                if len(to_delete_cmds) >= DELETE_STAT_NUM:
                    break

            # create cursor
            print(tuple(to_delete_cmds))
            cursor = self.conn.cursor()
            cursor.executemany(
                "delete from history where cmd = ?", to_delete_cmds)
            self.conn.commit()
            # 清理数据库
            cursor.execute("VACUUM")
            # 重新查询总数
            cursor.execute("select count(*) from history")
            count_result = cursor.fetchone()
            self.total_count = count_result[0]

            # 从history_stat删除
            for del_cmd_tuple in to_delete_cmds:
                del self.history_stat[del_cmd_tuple[0]]

    def append_to_histable(self):
        if len(self.new_history) == 0:
            return

        log_debug("write to history:" + str(self.new_history))

        his_list = []
        for his in self.new_history:
            his_list.append((his["session"], his["cmd"].encode('utf-8'),
                             his["pre_cmd"].encode(
                                 'utf-8'), his["prefix"].encode('utf-8'),
                             his.get("window_title", '').encode('utf-8'),
                             his["time"], his["interval"]))

        # create cursor
        cursor = self.conn.cursor()
        cursor.executemany('''INSERT INTO history(session,cmd,pre_cmd,prefix,window_title,time,interval) 
                VALUES(?,?,?,?,?,?,?)''', his_list)
        self.conn.commit()

        # 大于最大数量时，删除 DELETE_NUM 条，此时不同步更新 stat，没有大问题
        if self.total_count >= MAX_HIS_NUM:
            result = cursor.execute(
                "delete from history where id in (select id  from history order by id limit {})".format(DELETE_NUM))
            self.total_count = self.total_count - result.rowcount
            self.conn.commit()
            # 清理数据库
            cursor.execute("VACUUM")
            self.conn.commit()

        cursor.close()

        self.new_history = []
        self.last_append_time = nowTime()

    def add_history(self, history):
        # 长度小于2的直接不记录了
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
    def append_common_cmd(self, common_cmd, back_size):
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
            self.all_common_cmds.append(
                {'cmd': common_cmd, 'back_size': back_size, 'count': 1, 'time': nowTime()})

        log_debug("add common_cmd:" + common_cmd)
        # 添加一条新的 common历史记录
        cursor = self.conn.cursor()
        cursor.execute('''INSERT INTO common_cmd(cmd,back_size,count,time) 
                VALUES(?,?,?,?)''', (common_cmd, back_size, 1, nowTime()))
        self.conn.commit()

    # 获取最少使用的命令 count在5以下的  使用最少的一般可能是错误的，需要删除的
    def get_lfu_cmds(self, input=''):
        cmds = []
        for cmd, stat in self.history_stat.items():
            cmds.append(
                {"command": cmd, "count": stat["count"], "last_time": stat["last_time"]})

        sorted_cmds = sorted(cmds, key=lambda item: (
            item['count'], item['last_time']))
        if len(sorted_cmds) > 10:
            sorted_cmds = sorted_cmds[:10]

        return sorted_cmds

    # 删除命令记录
    def delete_cmd(self, del_cmd):
        log_debug("delete_cmd:" + del_cmd)
        cursor = self.conn.cursor()
        cursor.execute("delete from history where cmd = ?", (del_cmd,))
        cursor.close()
        self.conn.commit()

        del self.history_stat[del_cmd]
