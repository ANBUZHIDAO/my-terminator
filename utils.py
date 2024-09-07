#!/usr/bin/env python3
# -*-coding: UTF-8 -*-

import os
import sys
import time
import re
from collections import OrderedDict


def nowTime(): return int(round(time.time() * 1000))


cmd_pattern = re.compile(r'[^ ]+ (/)?')
INTERVAL_LEVEL = [5000, 10000, 15000, 60000, 300000, 600000]

DEBUG_ENABLE = False         # 是否打印DEBUG日志


def log_debug(*msgs):
    if DEBUG_ENABLE:
        filename = sys._getframe(1).f_code.co_filename
        if len(msgs) == 1:
            print("[DEBUG]: %s (%s:%d)" % (
                msgs[0], filename[filename.rfind('/')+1:], sys._getframe(1).f_lineno))
        else:
            print("[DEBUG]: %s (%s:%d)" % (
                msgs, filename[filename.rfind('/')+1:], sys._getframe(1).f_lineno))


def log_info(msg):
    print('\033[32m' + msg + "\033[0m")


def timing(f):
    def wrap(*args):
        time1 = time.time()
        ret = f(*args)
        time2 = time.time()
        print('%s function took %0.5f ms' %
              (f.func_name, (time2-time1)*1000.0))
        return ret
    return wrap


class LRUCache(OrderedDict):
    def __init__(self, size=128):
        self.size = size,
        self.cache = OrderedDict()

    def get(self, key):
        if key in self.cache:
            val = self.cache.pop(key)
            self.cache[key] = val
        else:
            val = None

        return val

    def set(self, key, val):
        if key in self.cache:
            val = self.cache.pop(key)
            self.cache[key] = val
        else:
            if len(self.cache) == self.size:
                self.cache.popitem(last=False)
                self.cache[key] = val
            else:
                self.cache[key] = val


def split_word(str):
    s_list = re.split("\W+", str)

    for i in range(len(s_list)-1, -1, -1):
        if s_list[i] == '':
            s_list.pop(i)

    return s_list


# simple two string match
str_match_cache = LRUCache(size=2000)
# @timing


def match_str_by_words(str1, str2):
    key = str1+":"+str2
    cache_match = str_match_cache.get(key)
    if cache_match is not None:
        return cache_match

    l_match = left_match_str_by_words(str1, str2)
    if l_match >= 0.5:
        str_match_cache.set(key, l_match)
        return l_match

    r_match = right_match_str_by_words(str1, str2)
    bigger_match = (l_match if (l_match > r_match) else r_match)
    str_match_cache.set(key, bigger_match)
    return bigger_match


def left_match_str(str1, str2):
    i, j = len(str1), len(str2)
    k = (i if (i < j) else j)
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
        return left_match_str(str1, str2)

    s1 = split_word(str1)
    s2 = split_word(str2)

    size1 = len(s1)
    size2 = len(s2)

    k = (size1 if (size1 < size2) else size2)
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
    i, j = len(str1), len(str2)
    k = (i if (i < j) else j)
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
        return right_match_str(str1, str2)

    s1 = split_word(str1)
    s2 = split_word(str2)

    size1 = len(s1)
    size2 = len(s2)

    k = (size1 if (size1 < size2) else size2)
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
# @timing


def prefix_match_str(str1, str2):
    key = str1+":"+str2
    cache_match = p_match_cache.get(key)
    if cache_match is not None:
        return cache_match

    # prefix是从后往前匹配，且去掉尾部可能存在的特殊字符
    prefix_match = right_match_str_by_words(str1, str2)
    p_match_cache.set(key, prefix_match)
    return prefix_match

# @timing


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
            common_cmd, _ = get_common_cmd(str1, str2)
            if common_cmd != '':
                return 0.8
            else:
                return 0

    return 0

# type: 1:prefix 2:precmd


def max_match_str(str1, strmaps, type=None):
    max_match = 0
    max_match_count = 0
    total = 0  # 总数
    kinds = 0  # 种类

    for str2, count in strmaps.items():
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


def get_common_cmd(str1, str2):

    if '"' in str1 or "'" in str1 or '"' in str2 or "'" in str2:
        return '', 0

    s1 = str1.split()
    s2 = str2.split()

    if len(s1) != len(s2) or len(s1) < 3 or s1[0] != s2[0]:
        return '', 0

    not_match_count = 0
    not_match_index = -1  # 不一样的部分的索引
    for index, substr in enumerate(s1):
        substr2 = s2[index]

        if substr != substr2:
            not_match_count = not_match_count + 1
            not_match_index = index

        if not_match_count > 1:
            break
    # 不同的部分不等于1 则不处理
    if not_match_count == 1:
        common_prefix = os.path.commonprefix(
            [s1[not_match_index], s2[not_match_index]])
        s1[not_match_index] = common_prefix
        common_cmd = " ".join(s1)
        return common_cmd, len(" ".join(s1[not_match_index:])) - len(common_prefix)

    return '', 0


def get_interval_level(interval):
    if interval == -1 or not interval:
        return 10
    for level, value in enumerate(INTERVAL_LEVEL):
        if interval <= value:
            return level


def by_score(suggest_cmd):
    return suggest_cmd["score"]
