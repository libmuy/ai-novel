#!/usr/bin/env python3
"""
02_工具/prune_character_sheet.py
根据当前卷号/阶段，切片剪裁主角档案，生成 01_设定/00_主角档案_当前阶段.md
"""
import sys
import os
import re

STAGE_MAP = {
    1: ("第一阶段", "凡人/炼气期"),
    2: ("第二阶段", "筑基期"),
    3: ("第三阶段", "金丹期中期"),
    4: ("第四阶段", "金丹期圆满"),
    5: ("第五阶段", "元婴期及以上"),
}

def prune_character_sheet(novel_dir, volume_num=1):
    setting_dir = os.path.join(novel_dir, "01_设定")
    src_file = os.path.join(setting_dir, "00_主角档案.md")
    dest_file = os.path.join(setting_dir, "00_主角档案_当前阶段.md")

    if not os.path.exists(src_file):
        print(f"ERROR: 找不到主角档案原文件 {src_file}")
        return False

    with open(src_file, "r", encoding="utf-8") as f:
        content = f.read()

    stage_key, stage_desc = STAGE_MAP.get(volume_num, ("第一阶段", "凡人/炼气期"))

    # 按行切割并筛选成长弧线中的阶段
    lines = content.splitlines()
    pruned_lines = []
    in_growth_arc = False

    for line in lines:
        if line.startswith("## 全书成长弧线"):
            in_growth_arc = True
            pruned_lines.append(line)
            continue
        elif line.startswith("## ") and in_growth_arc:
            in_growth_arc = False

        if in_growth_arc:
            # 匹配表格行中的阶段
            if line.strip().startswith("|") and ("阶段" in line or "状态" in line):
                if any(k in line for k in ["全书弧线状态", "出场状态", "最终状态", stage_key]):
                    pruned_lines.append(line)
                elif line.strip().startswith("| 字段 |") or line.strip().startswith("|------|"):
                    pruned_lines.append(line)
                else:
                    # 忽略其他非当前阶段行
                    continue
            else:
                pruned_lines.append(line)
        else:
            pruned_lines.append(line)

    header_notice = f"> 【当前阶段切片】基于 00_主角档案.md 剪裁 (适用卷: 卷{volume_num:02d} - {stage_key}: {stage_desc})\n\n"
    final_output = header_notice + "\n".join(pruned_lines) + "\n"

    with open(dest_file, "w", encoding="utf-8") as f:
        f.write(final_output)

    print(f"成功生成当前阶段主角卡: {dest_file} (卷{volume_num:02d} -> {stage_key})")
    return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        novel_path = "01_小说数据/00_苍玄"
        vol = 1
    else:
        novel_path = sys.argv[1]
        vol = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    prune_character_sheet(novel_path, vol)
