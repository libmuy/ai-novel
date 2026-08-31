#!/usr/bin/env python3
"""
批量替换所有TODO占位符为全局ID
用法: python3 02_工具/99_一次性脚本_归档/replace_todos.py
"""
import re
import os

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOVEL_DIR = os.path.join(WORKSPACE, "01_小说数据", "00_苍玄")

# ============================================================
# 映射表：(文件名模式, 旧ID, 新ID)
# ============================================================

# 地理区域文件中的内联引用 (势力: TODO-01~11, 人物: TODO-01~11, 伏笔: TODO-01~18)
GEO_FACTION_MAP = {
    "TODO-01": "TODO-FC-001",  # 控制矿区的低阶家族
    "TODO-02": "TODO-FC-002",  # 凡域散修联盟
    "TODO-03": "TODO-FC-003",  # 地方盗匪团伙
    "TODO-04": "TODO-FC-004",  # 主导坊市贸易的商会联盟
    "TODO-05": "TODO-FC-005",  # 地下黑市掌控者
    "TODO-06": "TODO-FC-006",  # 天域派驻的监察势力
    "TODO-07": "TODO-FC-007",  # 天域主导宗门
    "TODO-08": "TODO-FC-008",  # 天域世家联盟
    "TODO-09": "TODO-FC-009",  # 散修高阶联盟
    "TODO-10": "TODO-FC-010",  # 禁地外围监视势力
    "TODO-11": "TODO-FC-011",  # 探索禁地的秘密组织
}

GEO_PERSON_MAP = {
    "TODO-01": "TODO-CH-001",  # 矿务司管事
    "TODO-02": "TODO-CH-002",  # 矿城长者
    "TODO-03": "TODO-CH-003",  # 断骨坡寨主
    "TODO-04": "TODO-CH-004",  # 九曲长街掌柜
    "TODO-05": "TODO-CH-005",  # 墨骨巷情报贩子
    "TODO-06": "TODO-CH-006",  # 天璇圣城世家长老
    "TODO-07": "TODO-CH-007",  # 九宗演武场裁判长
    "TODO-08": "TODO-CH-008",  # 苍玄祖脉守卫者
    "TODO-09": "TODO-CH-008",  # 苍玄祖脉守卫者（复用）
    "TODO-10": "TODO-CH-009",  # 九宗演武场副裁判
    "TODO-11": "TODO-CH-008",  # 苍玄祖脉守卫者（复用）
}

GEO_FH_MAP = {
    "TODO-01": "TODO-FH-001",  # 位面碰撞的真相
    "TODO-02": "TODO-FH-002",  # 封印衰减的危机
    "TODO-03": "TODO-FH-003",  # 枯港矿城暗矿脉上古遗迹
    "TODO-04": "TODO-FH-004",  # 枯港矿城矿神传说
    "TODO-05": "TODO-FH-005",  # 断骨坡血煞道古战场
    "TODO-06": "TODO-FH-006",  # 断骨坡血月异象
    "TODO-07": "TODO-FH-007",  # 九曲长街地下暗河遗迹
    "TODO-08": "TODO-FH-008",  # 九曲长街灵石潮汐
    "TODO-09": "TODO-FH-009",  # 墨骨巷幽冥道古阵
    "TODO-10": "TODO-FH-010",  # 墨骨巷无面人传说
    "TODO-11": "TODO-FH-011",  # 天璇圣城老祖秘辛
    "TODO-12": "TODO-FH-012",  # 天璇圣城世家把柄
    "TODO-13": "TODO-FH-013",  # 九宗演武场演武碑
    "TODO-14": "TODO-FH-014",  # 九宗演武场天道裁决
    "TODO-15": "TODO-FH-015",  # 苍玄祖脉龙魂
    "TODO-16": "TODO-FH-016",  # 苍玄祖脉灵气同步
    "TODO-17": "TODO-FH-017",  # 九幽渊冥河之门
    "TODO-18": "TODO-FH-018",  # 九幽渊黄泉宗遗迹
}

# 道义文件中的书籍TODO (TODO-01~06)
DAOYI_BOOK_MAP = {
    "TODO-01": "TODO-BK-001",
    "TODO-02": "TODO-BK-002",
    "TODO-03": "TODO-BK-003",
    "TODO-04": "TODO-BK-004",
    "TODO-05": "TODO-BK-005",
    "TODO-06": "TODO-BK-006",
}

# 势力组织总索引中的TODO (TODO-001~004)
FACTION_INDEX_MAP = {
    "TODO-001": "TODO-FC-012",
    "TODO-002": "TODO-FC-013",
    "TODO-003": "TODO-FC-014",
    "TODO-004": "TODO-FC-015",
}

# 资源总索引中的TODO (TODO-001~012)
RESOURCE_INDEX_MAP = {
    "TODO-001": "TODO-DN-001",
    "TODO-002": "TODO-DN-002",
    "TODO-003": "TODO-DN-003",
    "TODO-004": "TODO-DN-004",
    "TODO-005": "TODO-DN-005",
    "TODO-006": "TODO-DN-006",
    "TODO-007": "TODO-DN-007",
    "TODO-008": "TODO-FC-016",
    "TODO-009": "TODO-FC-017",
    "TODO-010": "TODO-FC-018",
    "TODO-011": "TODO-FC-019",
    "TODO-012": "TODO-CH-010",
}

# 各资源子文件中的TODO映射
RESOURCE_FILE_MAPS = {
    "04_资源_货币.md": {
        "TODO-001": "TODO-DN-001",
        "TODO-002": "TODO-DN-002",
        "TODO-003": "TODO-DN-003",
        "TODO-004": "TODO-FC-016",  # table-only
        "TODO-005": "TODO-FC-017",
    },
    "04_资源_丹药.md": {
        "TODO-006": "TODO-DN-004",
        "TODO-007": "TODO-DN-005",
        "TODO-008": "TODO-FC-016",
        "TODO-009": "TODO-FC-017",
    },
    "04_资源_法宝.md": {
        "TODO-010": "TODO-DN-008",
        "TODO-011": "TODO-DN-009",
        "TODO-012": "TODO-FC-016",
        "TODO-013": "TODO-FC-018",  # table-only
        "TODO-014": "TODO-DN-010",
    },
    "04_资源_功法.md": {
        "TODO-015": "TODO-FC-016",
        "TODO-016": "TODO-FC-018",
        "TODO-017": "TODO-DN-011",
    },
    "04_资源_材料.md": {
        "TODO-018": "TODO-DN-012",
        "TODO-019": "TODO-DN-013",
        "TODO-020": "TODO-DN-014",
        "TODO-021": "TODO-DN-015",
    },
    "04_资源_妖兽.md": {
        "TODO-022": "TODO-DN-016",
        "TODO-023": "TODO-DN-017",
        "TODO-024": "TODO-DN-018",
        "TODO-025": "TODO-FC-017",
    },
    "04_资源_符箓.md": {
        "TODO-026": "TODO-DN-019",
        "TODO-027": "TODO-DN-020",
        "TODO-028": "TODO-FC-016",
        "TODO-029": "TODO-FC-019",
    },
    "04_资源_秘境.md": {
        "TODO-030": "TODO-DN-021",
        "TODO-031": "TODO-DN-022",
        "TODO-032": "TODO-DN-023",  # table-only
        "TODO-033": "TODO-DN-024",
        "TODO-034": "TODO-FC-017",
    },
    "04_资源_经济.md": {
        "TODO-035": "TODO-FC-016",
        "TODO-036": "TODO-FC-017",
        "TODO-037": "TODO-FC-018",
        "TODO-038": "TODO-FC-019",
        "TODO-039": "TODO-CH-010",  # table-only
    },
}

# 势力组织子文件中的TODO映射
FACTION_FILE_MAPS = {
    "03_势力组织_云璃商会.md": {
        "TODO-001": "TODO-FC-012",
        "TODO-006": "TODO-FC-020",
    },
    "03_势力组织_紫极仙宗.md": {
        "TODO-002": "TODO-FC-013",
    },
    "03_势力组织_鬼手阁.md": {
        "TODO-003": "TODO-FC-014",
    },
    "03_势力组织_守脉人.md": {
        "TODO-004": "TODO-FC-015",
    },
    "03_势力组织_黑石会.md": {
        "TODO-005": "TODO-FC-021",
    },
}


def replace_in_file(filepath, replacements):
    """Replace all occurrences in a file according to the replacements dict.
    Each key in replacements should be a (prefix, old_id) tuple or a bare string.
    If tuple: only replaces @{prefix}.[old_id] pattern.
    If string: replaces bare TODO-xxx at word boundary.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    original = content
    for key, new in replacements.items():
        if isinstance(key, tuple):
            prefix, old_id = key
            content = content.replace(f"@{prefix}.[{old_id}]", f"@{prefix}.[{new}]")
        else:
            # Bare TODO-xxx replacement (for definition tables)
            content = re.sub(r'\b' + re.escape(key) + r'(?![a-zA-Z0-9])', new, content)

    if content != original:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return True
    return False


def process_geography_files():
    """Process all geography files."""
    geo_dir = os.path.join(NOVEL_DIR, "02_数据库", "02_地理区域")
    changed = []

    for fname in os.listdir(geo_dir):
        if not fname.endswith('.md') or fname == '02_地理区域.md':
            continue
        filepath = os.path.join(geo_dir, fname)
        replacements = {}

        # Apply faction mappings with @势力 prefix
        for old, new in GEO_FACTION_MAP.items():
            replacements[("势力", old)] = new
        # Apply person mappings with @人物 prefix
        for old, new in GEO_PERSON_MAP.items():
            replacements[("人物", old)] = new
        # Apply foreshadowing mappings with @伏笔 prefix
        for old, new in GEO_FH_MAP.items():
            replacements[("伏笔", old)] = new

        if replace_in_file(filepath, replacements):
            changed.append(fname)

    return changed


def process_daoyi_file():
    """Process the daoyi file."""
    filepath = os.path.join(NOVEL_DIR, "01_设定", "05_核心道义.md")
    replacements = {}
    for old, new in DAOYI_BOOK_MAP.items():
        replacements[("书籍", old)] = new
        replacements[old] = new  # Also bare TODO in definition table
    if replace_in_file(filepath, replacements):
        return ["05_核心道义.md"]
    return []


def process_faction_index():
    """Process faction index file."""
    filepath = os.path.join(NOVEL_DIR, "02_数据库", "03_势力组织", "03_势力组织.md")
    replacements = {}
    for old, new in FACTION_INDEX_MAP.items():
        replacements[old] = new  # Bare TODO in definition table
    if replace_in_file(filepath, replacements):
        return ["03_势力组织.md"]
    return []


def process_faction_files():
    """Process individual faction files."""
    faction_dir = os.path.join(NOVEL_DIR, "02_数据库", "03_势力组织")
    changed = []

    for fname, mappings in FACTION_FILE_MAPS.items():
        filepath = os.path.join(faction_dir, fname)
        replacements = {}
        for old, new in mappings.items():
            replacements[("势力", old)] = new
            replacements[old] = new  # Also bare TODO in definition table
        if replace_in_file(filepath, replacements):
            changed.append(fname)

    return changed


def process_resource_index():
    """Process resource index file."""
    filepath = os.path.join(NOVEL_DIR, "02_数据库", "04_资源", "04_资源.md")
    replacements = {}
    for old, new in RESOURCE_INDEX_MAP.items():
        replacements[old] = new  # Bare TODO in definition table
    if replace_in_file(filepath, replacements):
        return ["04_资源.md"]
    return []


def process_resource_files():
    """Process individual resource files."""
    res_dir = os.path.join(NOVEL_DIR, "02_数据库", "04_资源")
    changed = []

    for fname, mappings in RESOURCE_FILE_MAPS.items():
        filepath = os.path.join(res_dir, fname)
        replacements = {}
        for old, new in mappings.items():
            replacements[("类型", old)] = new
            replacements[old] = new  # Also bare TODO in definition table
        if replace_in_file(filepath, replacements):
            changed.append(fname)

    return changed


def verify_no_old_todos():
    """Verify no old TODO references remain in data files."""
    issues = []
    data_dirs = [
        os.path.join(NOVEL_DIR, "01_设定"),
        os.path.join(NOVEL_DIR, "02_数据库"),
    ]

    # Pattern to match old-style TODO references (not the new global format)
    old_pattern = re.compile(r'@(势力|人物|伏笔|类型|书籍)\.\[TODO-(?!FC-|CH-|FH-|BK-|DN-)\d')

    for data_dir in data_dirs:
        for root, dirs, files in os.walk(data_dir):
            for fname in files:
                if not fname.endswith('.md'):
                    continue
                filepath = os.path.join(root, fname)
                with open(filepath, 'r', encoding='utf-8') as f:
                    for i, line in enumerate(f, 1):
                        if old_pattern.search(line):
                            rel = os.path.relpath(filepath, NOVEL_DIR)
                            issues.append(f"{rel}:{i}: {line.strip()}")

    return issues


def main():
    print("=" * 60)
    print("TODO全局替换脚本")
    print("=" * 60)

    all_changed = []

    print("\n[1/6] 处理地理区域文件...")
    changed = process_geography_files()
    all_changed.extend(changed)
    print(f"  修改了 {len(changed)} 个文件: {', '.join(changed)}")

    print("\n[2/6] 处理道义文件...")
    changed = process_daoyi_file()
    all_changed.extend(changed)
    print(f"  修改了 {len(changed)} 个文件: {', '.join(changed)}")

    print("\n[3/6] 处理势力组织总索引...")
    changed = process_faction_index()
    all_changed.extend(changed)
    print(f"  修改了 {len(changed)} 个文件: {', '.join(changed)}")

    print("\n[4/6] 处理势力组织子文件...")
    changed = process_faction_files()
    all_changed.extend(changed)
    print(f"  修改了 {len(changed)} 个文件: {', '.join(changed)}")

    print("\n[5/6] 处理资源总索引...")
    changed = process_resource_index()
    all_changed.extend(changed)
    print(f"  修改了 {len(changed)} 个文件: {', '.join(changed)}")

    print("\n[6/6] 处理资源子文件...")
    changed = process_resource_files()
    all_changed.extend(changed)
    print(f"  修改了 {len(changed)} 个文件: {', '.join(changed)}")

    print("\n" + "=" * 60)
    print(f"总计修改: {len(all_changed)} 个文件")
    print("=" * 60)

    print("\n验证剩余旧式TODO引用...")
    issues = verify_no_old_todos()
    if issues:
        print(f"  发现 {len(issues)} 处未替换的旧式TODO引用:")
        for issue in issues:
            print(f"    {issue}")
    else:
        print("  验证通过：所有旧式TODO引用已替换完成")

    return len(all_changed)


if __name__ == "__main__":
    main()
