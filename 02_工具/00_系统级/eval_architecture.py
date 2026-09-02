#!/usr/bin/env python3
"""
AI 小说生成系统架构量化评估与测试脚本
用于分析：
1. 现有路由表及典型任务在标准上下文下的 Token 占用（Token 效率）
2. 百万字至千万字（3000万字/3000+章）场景下的文件数、索引膨胀率、状态台账扩展性推算
3. 状态一致性、@引用链路解析瓶颈测试
"""

import os
import sys
import glob
import math

def estimate_tokens(text):
    """
    粗略估计 中文/英文/符号 的 Token 数
    中文约 1.5 - 2 字符/Token，这里按平均 1.5 字符 = 1 Token 计算 (即 char_len / 1.5)
    """
    if not text:
        return 0
    return int(len(text) / 1.5)

def analyze_token_efficiency(base_dir):
    print("=== 1. 大模型 Token 效率与路由表上下文分析 ===")

    # 模拟各类任务需要读取的文件
    tasks = {
        "写单章正文": [
            "00_通用模板/01_写作规则/01_系统指令.md",
            "00_通用模板/01_写作规则/00_通用写作规则_生成版.md",
            "00_通用模板/01_写作规则/00_文风_底层成长流.md",
            "01_小说数据/00_苍玄/01_设定/00_主角档案_当前阶段.md",
            "01_小说数据/00_苍玄/01_设定/00_小说概念.md",
            "01_小说数据/00_苍玄/03_规划/01_第01部/01_卷01/规划_卷章0001.md",
            "01_小说数据/00_苍玄/02_数据库/07_人物/07_人物_刘三斤.md",
            "01_小说数据/00_苍玄/05_工作区/02_状态/01_最新状态/01_角色/01_角色_示例角色.md"
        ],
        "生成单卷大纲": [
            "00_通用模板/02_卡片模板/06_卷大纲模板.md",
            "00_通用模板/01_写作规则/00_通用写作规则_生成版.md",
            "01_小说数据/00_苍玄/01_设定/00_主角档案_当前阶段.md",
            "01_小说数据/00_苍玄/01_设定/05_核心道义.md",
            "01_小说数据/00_苍玄/03_规划/规划.md",
            "01_小说数据/00_苍玄/02_数据库/01_修炼体系/01_修炼体系.md",
            "01_小说数据/00_苍玄/02_数据库/02_地理区域/02_地理区域.md",
            "01_小说数据/00_苍玄/02_数据库/03_势力组织/03_势力组织.md",
            "01_小说数据/00_苍玄/02_数据库/04_资源/04_资源.md"
        ],
        "云端回填分割": [
            "00_通用模板/01_写作规则/07_云端回填分割规则.md",
            "01_小说数据/00_苍玄/00_进度.md"
        ]
    }

    for task_name, files in tasks.items():
        total_chars = 0
        total_tokens = 0
        file_details = []
        for fp in files:
            if os.path.exists(fp):
                with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    c_len = len(content)
                    t_len = estimate_tokens(content)
                    total_chars += c_len
                    total_tokens += t_len
                    file_details.append((os.path.basename(fp), c_len, t_len))
            else:
                file_details.append((os.path.basename(fp) + " (未找到)", 0, 0))

        print(f"\n任务 [{task_name}]:")
        print(f"  包含文件数: {len(files)}")
        print(f"  总字符数: {total_chars:,} | 预估 Context Token 占用: {total_tokens:,} Tokens")
        for fname, cl, tl in file_details:
            print(f"    - {fname}: {cl:,} chars (~{tl:,} tokens)")

def analyze_scalability_10m_words():
    print("\n=== 2. 千万字（10,000,000字 / ~3,300章 / 30部 / 100卷）扩展性模拟推算 ===")

    # 假设参数
    total_words = 10_000_000
    words_per_chapter = 3000
    total_chapters = math.ceil(total_words / words_per_chapter) # ~3333章
    volumes = 100 # 约100卷
    parts = 20 # 约20部

    # 算规划层与创作层文件数
    planning_chapter_files = total_chapters # 每章一个单章细纲 规划_卷XX_章XXXX.md
    planning_event_files = total_chapters * 1.5 # 平均每章1.5个事件卡(战斗/突破/抉择)
    text_files = total_chapters # 每章一个正文文件

    # 算数据库实体数（千万字玄幻小说规模）
    characters = 800 # 角色/NPC
    locations = 300 # 地理节点/秘境/山脉
    factions = 150 # 宗门/皇朝/商会
    resources = 600 # 功法/法宝/丹药/灵药/材料
    foreshadowing = 1200 # 伏笔条目

    total_db_files = characters + locations + factions + resources + foreshadowing
    total_files = planning_chapter_files + planning_event_files + text_files + total_db_files + 50 # 杂项/系统文件

    # 单个文件与总索引推算
    foreshadowing_index_lines = foreshadowing
    foreshadowing_index_bytes = foreshadowing_index_lines * 120 # 每条约120字节

    planning_index_lines = parts + volumes + total_chapters
    planning_index_bytes = planning_index_lines * 150

    state_ledger_lines = (characters + factions) * 10 # 状态快照

    print(f"  [规模参数]")
    print(f"    - 目标总字数: {total_words:,} 字")
    print(f"    - 预估总章节数: {total_chapters:,} 章 (按 3,000字/章)")
    print(f"    - 划分部数: {parts} 部 | 卷数: {volumes} 卷")

    print(f"  [文件节点数估算]")
    print(f"    - 正文文件 (`10_正文/`): {text_files:,} 个")
    print(f"    - 细纲规划文件 (`03_规划/`): {planning_chapter_files:,} 个细纲 + {int(planning_event_files):,} 个事件卡")
    print(f"    - 数据库实体文件 (`02_数据库/`): {total_db_files:,} 个")
    print(f"    - 全局项目物理文件总数: {int(total_files):,} 个文件")

    print(f"  [核心索引文件 Token 膨胀压力]")
    print(f"    - 伏笔总索引 (`08_伏笔登记.md`): {foreshadowing:,} 条 | 预估大小: ~{foreshadowing_index_bytes/1024:.1f} KB | 预估 Token: ~{estimate_tokens('a'*foreshadowing_index_bytes):,} Tokens")
    print(f"    - 全书规划总索引 (`03_规划/规划.md`): {planning_chapter_files + volumes} 行 | 预估大小: ~{planning_index_bytes/1024:.1f} KB | 预估 Token: ~{estimate_tokens('a'*planning_index_bytes):,} Tokens")
    print(f"    - 全局状态 (`05_工作区/02_状态/01_最新状态/`，每对象一文件): 涉及 {characters} 角色, 单对象文件预估 ~{estimate_tokens('a'*800):,} Tokens（不再有单文件膨胀）")

def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    analyze_token_efficiency(base_dir)
    analyze_scalability_10m_words()

if __name__ == "__main__":
    main()
