#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
工作区目录结构迁移脚本 (migrate_workspace_arch.py)
用于将旧版 05_工作区 架构迁移为统一的递归作用域架构：
全书 / 部 / 卷 / 章 均遵循：
  00_提示词
  01_模型输出
  02_状态
  [下一级小说结构/03_...]
"""

import os
import shutil


def safe_move_contents(src_dir, dst_dir):
    if not os.path.exists(src_dir):
        return
    os.makedirs(dst_dir, exist_ok=True)
    for item in os.listdir(src_dir):
        s = os.path.join(src_dir, item)
        d = os.path.join(dst_dir, item)
        if os.path.isdir(s):
            safe_move_contents(s, d)
        else:
            if not os.path.exists(d):
                shutil.move(s, d)
            else:
                os.remove(s)
    if os.path.exists(src_dir) and not os.listdir(src_dir):
        os.rmdir(src_dir)


def migrate_skeleton(skeleton_ws):
    print(f"Migrating skeleton workspace at {skeleton_ws}...")

    os.makedirs(os.path.join(skeleton_ws, "00_提示词"), exist_ok=True)
    os.makedirs(os.path.join(skeleton_ws, "01_模型输出"), exist_ok=True)
    os.makedirs(os.path.join(skeleton_ws, "02_状态"), exist_ok=True)

    # 迁移提示词 01_提示词/00_说明.md -> 00_提示词/00_说明.md
    old_prompt_desc = os.path.join(skeleton_ws, "01_提示词", "00_说明.md")
    if os.path.exists(old_prompt_desc):
        shutil.move(old_prompt_desc, os.path.join(skeleton_ws, "00_提示词", "00_说明.md"))
    if os.path.exists(os.path.join(skeleton_ws, "01_提示词")):
        shutil.rmtree(os.path.join(skeleton_ws, "01_提示词"))

    # 迁移 00_历史回填 -> 01_模型输出
    old_history = os.path.join(skeleton_ws, "00_历史回填")
    if os.path.exists(old_history):
        shutil.rmtree(old_history)

    # 迁移 00_全局/ -> 02_状态/
    old_global = os.path.join(skeleton_ws, "00_全局")
    if os.path.exists(old_global):
        if os.path.exists(os.path.join(old_global, "00_基线状态")):
            shutil.move(os.path.join(old_global, "00_基线状态"), os.path.join(skeleton_ws, "02_状态", "00_基线状态"))
        if os.path.exists(os.path.join(old_global, "01_最新状态")):
            shutil.move(os.path.join(old_global, "01_最新状态"), os.path.join(skeleton_ws, "02_状态", "01_最新状态"))
        if os.path.exists(os.path.join(old_global, "00_技能")):
            shutil.move(os.path.join(old_global, "00_技能"), os.path.join(skeleton_ws, "02_状态", "02_技能"))
        if os.path.exists(os.path.join(old_global, "00_状态对象白名单.md")):
            shutil.move(os.path.join(old_global, "00_状态对象白名单.md"), os.path.join(skeleton_ws, "02_状态", "03_状态对象白名单.md"))
        shutil.rmtree(old_global)

    # 迁移 部 01_第01部 -> 03_第01部
    old_part = os.path.join(skeleton_ws, "01_第01部")
    new_part = os.path.join(skeleton_ws, "03_第01部")
    if os.path.exists(old_part):
        safe_move_contents(old_part, new_part)

    os.makedirs(os.path.join(new_part, "00_提示词"), exist_ok=True)
    os.makedirs(os.path.join(new_part, "01_模型输出"), exist_ok=True)
    os.makedirs(os.path.join(new_part, "02_状态"), exist_ok=True)

    # 卷 01_卷01 -> 03_卷01
    old_vol = os.path.join(new_part, "01_卷01")
    new_vol = os.path.join(new_part, "03_卷01")
    if os.path.exists(old_vol):
        safe_move_contents(old_vol, new_vol)

    os.makedirs(os.path.join(new_vol, "00_提示词"), exist_ok=True)
    os.makedirs(os.path.join(new_vol, "01_模型输出"), exist_ok=True)
    os.makedirs(os.path.join(new_vol, "02_状态"), exist_ok=True)

    # 章 章0001 -> 03_章0001
    old_chap = os.path.join(new_vol, "章0001")
    new_chap = os.path.join(new_vol, "03_章0001")
    if os.path.exists(old_chap):
        safe_move_contents(old_chap, new_chap)

    os.makedirs(os.path.join(new_chap, "00_提示词"), exist_ok=True)
    os.makedirs(os.path.join(new_chap, "01_模型输出"), exist_ok=True)
    os.makedirs(os.path.join(new_chap, "02_状态"), exist_ok=True)

    old_opener = os.path.join(new_chap, "03_本章开篇状态.md")
    if os.path.exists(old_opener):
        shutil.move(old_opener, os.path.join(new_chap, "02_状态", "00_开篇状态.md"))

    old_changelog = os.path.join(new_chap, "04_本章状态履历.md")
    if os.path.exists(old_changelog):
        shutil.move(old_changelog, os.path.join(new_chap, "02_状态", "01_状态履历.md"))


def migrate_cangxuan_workspace(ws_dir):
    print(f"Migrating Cangxuan workspace at {ws_dir}...")

    # 1. 创建全书级目录与各级占位
    os.makedirs(os.path.join(ws_dir, "00_提示词"), exist_ok=True)
    os.makedirs(os.path.join(ws_dir, "01_模型输出"), exist_ok=True)
    os.makedirs(os.path.join(ws_dir, "02_状态"), exist_ok=True)

    # 提前保证新部/卷/章层级存在
    new_part = os.path.join(ws_dir, "03_第01部")
    new_vol = os.path.join(new_part, "03_卷01")
    new_chap = os.path.join(new_vol, "03_章0001")

    os.makedirs(os.path.join(new_part, "00_提示词"), exist_ok=True)
    os.makedirs(os.path.join(new_part, "01_模型输出"), exist_ok=True)
    os.makedirs(os.path.join(new_part, "02_状态"), exist_ok=True)

    os.makedirs(os.path.join(new_vol, "00_提示词"), exist_ok=True)
    os.makedirs(os.path.join(new_vol, "01_模型输出"), exist_ok=True)
    os.makedirs(os.path.join(new_vol, "02_状态"), exist_ok=True)

    os.makedirs(os.path.join(new_chap, "00_提示词"), exist_ok=True)
    os.makedirs(os.path.join(new_chap, "01_模型输出"), exist_ok=True)
    os.makedirs(os.path.join(new_chap, "02_状态"), exist_ok=True)

    # 迁移 00_全局/ -> 02_状态/
    old_global = os.path.join(ws_dir, "00_全局")
    if os.path.exists(old_global):
        if os.path.exists(os.path.join(old_global, "00_基线状态")):
            shutil.move(os.path.join(old_global, "00_基线状态"), os.path.join(ws_dir, "02_状态", "00_基线状态"))
        if os.path.exists(os.path.join(old_global, "01_最新状态")):
            shutil.move(os.path.join(old_global, "01_最新状态"), os.path.join(ws_dir, "02_状态", "01_最新状态"))
        if os.path.exists(os.path.join(old_global, "00_技能")):
            shutil.move(os.path.join(old_global, "00_技能"), os.path.join(ws_dir, "02_状态", "02_技能"))
        if os.path.exists(os.path.join(old_global, "00_状态对象白名单.md")):
            shutil.move(os.path.join(old_global, "00_状态对象白名单.md"), os.path.join(ws_dir, "02_状态", "03_状态对象白名单.md"))
        if os.path.exists(os.path.join(old_global, "00_描述合并缓存.jsonl")):
            shutil.move(os.path.join(old_global, "00_描述合并缓存.jsonl"), os.path.join(ws_dir, "02_状态", "04_描述合并缓存.jsonl"))
        if os.path.exists(old_global):
            shutil.rmtree(old_global)

    # 迁移旧 01_第01部 中的内容到 03_第01部
    old_part = os.path.join(ws_dir, "01_第01部")
    if os.path.exists(old_part):
        old_vol = os.path.join(old_part, "01_卷01")
        if os.path.exists(old_vol):
            # 迁移 00_卷01大纲校验记录.md
            vol_check = os.path.join(old_vol, "00_卷01大纲校验记录.md")
            if os.path.exists(vol_check):
                shutil.move(vol_check, os.path.join(new_vol, "02_状态", "00_大纲校验记录.md"))

            # 迁移章
            old_chap = os.path.join(old_vol, "章0001")
            if os.path.exists(old_chap):
                chap_file_map = [
                    ("03_本章开篇状态.md", "00_开篇状态.md"),
                    ("04_本章状态履历.md", "01_状态履历.md"),
                    ("05_正文校验记录.md", "02_正文校验记录.md"),
                    ("06_细纲对照记录.md", "03_细纲对照记录.md"),
                ]
                for old_f, new_f in chap_file_map:
                    old_p = os.path.join(old_chap, old_f)
                    if os.path.exists(old_p):
                        shutil.move(old_p, os.path.join(new_chap, "02_状态", new_f))
                if os.path.exists(old_chap) and not os.listdir(old_chap):
                    os.rmdir(old_chap)
            if os.path.exists(old_vol) and not os.listdir(old_vol):
                os.rmdir(old_vol)
        if os.path.exists(old_part) and not os.listdir(old_part):
            os.rmdir(old_part)

    # 迁移 01_提示词 -> 各级 00_提示词/
    old_prompt_dir = os.path.join(ws_dir, "01_提示词")
    if os.path.exists(old_prompt_dir):
        prompts = [
            ("00_说明.md", "00_说明.md", "global"),
            ("00_小说概念与定位提示.md", "01_小说概念与定位.md", "global"),
            ("01_修炼体系提示.md", "02_修炼体系.md", "global"),
            ("02_地理区域提示.md", "03_地理区域.md", "global"),
            ("03_势力组织提示.md", "04_势力组织.md", "global"),
            ("04_资源提示.md", "05_资源.md", "global"),
            ("05_主角与核心配角提示.md", "06_主角与核心配角.md", "global"),
            ("06_道义体系提示.md", "07_道义体系.md", "global"),
            ("07_文风节奏与爽感提示.md", "08_文风节奏与爽感.md", "global"),
            ("08_书籍库提示.md", "09_书籍库.md", "global"),
            ("09_全书卷大纲提示.md", "10_全书卷大纲.md", "global"),
            ("清理-CH_人物类占位符提示.md", "11_清理-CH_人物类占位符.md", "global"),
            ("清理-FC_势力类占位符提示.md", "12_清理-FC_势力类占位符.md", "global"),
            ("清理-DN_地名类占位符提示.md", "13_清理-DN_地名类占位符.md", "global"),
            ("清理-BK_书籍类占位符提示.md", "14_清理-BK_书籍类占位符.md", "global"),
            ("清理-FH_伏笔类占位符提示.md", "15_清理-FH_伏笔类占位符.md", "global"),
            ("10_单卷大纲生成_卷1提示.md", "00_单卷大纲生成_卷1.md", "vol1"),
            ("11_单章细纲_章0001提示.md", "00_单章细纲.md", "chap1"),
            ("章0001_正文提示.md", "01_正文生成.md", "chap1"),
        ]

        for src, dst, scope in prompts:
            src_p = os.path.join(old_prompt_dir, src)
            if os.path.exists(src_p):
                if scope == "global":
                    target_dir = os.path.join(ws_dir, "00_提示词")
                elif scope == "vol1":
                    target_dir = os.path.join(new_vol, "00_提示词")
                elif scope == "chap1":
                    target_dir = os.path.join(new_chap, "00_提示词")
                os.makedirs(target_dir, exist_ok=True)
                shutil.move(src_p, os.path.join(target_dir, dst))

        if os.path.exists(old_prompt_dir) and not os.listdir(old_prompt_dir):
            shutil.rmtree(old_prompt_dir)

    # 迁移 00_历史回填 -> 各级 01_模型输出/
    old_history = os.path.join(ws_dir, "00_历史回填")
    if os.path.exists(old_history):
        cloud_sub = os.path.join(old_history, "00_历史回填")
        if os.path.exists(cloud_sub):
            for fn in os.listdir(cloud_sub):
                src_f = os.path.join(cloud_sub, fn)
                dst_f = os.path.join(ws_dir, "01_模型输出", fn)
                shutil.move(src_f, dst_f)

        cloud_out = os.path.join(old_history, "00_云端输出")
        if os.path.exists(cloud_out):
            for fn in os.listdir(cloud_out):
                src_f = os.path.join(cloud_out, fn)
                if fn == "任务10_输出.md":
                    dst_f = os.path.join(new_vol, "01_模型输出", "00_单卷大纲生成_卷1.md")
                elif fn == "任务11_章0001_输出.md":
                    dst_f = os.path.join(new_chap, "01_模型输出", "00_单章细纲.md")
                elif fn == "章0001_正文_输出.md":
                    dst_f = os.path.join(new_chap, "01_模型输出", "01_正文生成.md")
                elif "章0001" in fn or "任务11" in fn:
                    dst_f = os.path.join(new_chap, "01_模型输出", fn)
                else:
                    dst_f = os.path.join(ws_dir, "01_模型输出", fn)
                os.makedirs(os.path.dirname(dst_f), exist_ok=True)
                shutil.move(src_f, dst_f)

        summary_file = os.path.join(old_history, "任务概要_势力人物伏笔回填.md")
        if os.path.exists(summary_file):
            shutil.move(summary_file, os.path.join(ws_dir, "01_模型输出", "任务概要_势力人物伏笔回填.md"))

        shutil.rmtree(old_history)


if __name__ == "__main__":
    skeleton = os.path.abspath("00_通用模板/05_项目骨架模板/05_工作区")
    cangxuan = os.path.abspath("01_小说数据/00_苍玄/05_工作区")
    migrate_cangxuan_workspace(cangxuan)
    migrate_skeleton(skeleton)
    print("Workspace migration complete!")
