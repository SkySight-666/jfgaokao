#!/usr/bin/env python3
"""掌上高考福建分数线数据查询服务"""
import http.server
import json
import sqlite3
import os
import re
import urllib.parse

PORT = 8899
MAJOR_DIR_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "专业目录.txt")


def build_static_tree(path):
    """从 专业目录.txt 解析三级标准专业树"""
    tree = {}
    cur_l2, cur_l3 = None, None
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if re.match(r'^\d{2} ', line):
                cur_l2 = line.split(' ', 1)[1].strip()
                tree.setdefault(cur_l2, {})
                cur_l3 = None
            elif re.match(r'^\d{4} ', line):
                name = line.split(' ', 1)[1].strip()
                cur_l3 = name if not re.match(r'^\d+$', name) else cur_l2 + '类'
                if cur_l2:
                    tree[cur_l2].setdefault(cur_l3, [])
            elif re.match(r'^\d{6}', line):
                sp = re.sub(r'^\d{6}[A-Z]*\s*', '', line).strip()
                if not sp:
                    continue
                if not cur_l3 and cur_l2:
                    cur_l3 = cur_l2 + '类'
                    tree[cur_l2].setdefault(cur_l3, [])
                if cur_l3 and sp not in tree[cur_l2].get(cur_l3, []):
                    tree[cur_l2][cur_l3].append(sp)
    for l2 in tree:
        for l3 in tree[l2]:
            tree[l2][l3].sort()
    return tree


STATIC_TREE = build_static_tree(MAJOR_DIR_FILE)


def major_search_keywords(raw):
    """从原始专业名提取搜索关键词：去掉(注:...)等批注，返回核心词用于LIKE搜索"""
    return re.sub(r'（[^）]*(?:注|授予|原专业代码|可授)[^）]*）', '', raw).strip()


def _load_list(path):
    """读名单文件，返回规范化名称集合"""
    names = set()
    if not os.path.exists(path):
        return names
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                names.add(line.replace('(', '（').replace(')', '）'))
    return names


_985 = _load_list(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "985名单.txt"))
_211 = _load_list(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "211名单.txt"))
_syl = _load_list(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "双一流名单.txt"))


def school_tags(school_name):
    """返回学校的标签列表，如 ['985', '211']"""
    name = school_name.replace('(', '（').replace(')', '）')
    tags = []
    if name in _985:
        tags.append('985')
    if name in _211:
        tags.append('211')
    if name in _syl:
        tags.append('双一流')
    return tags


DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "gaokao_fujian.db")


def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def query_db(sql, params=()):
    conn = get_conn()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


RANK_EXPR = """
CASE
    WHEN min_section IS NULL OR min_section = '' OR min_section = '-' OR CAST(min_section AS INTEGER) <= 0
    THEN 49
    ELSE CAST(min_section AS INTEGER)
END
"""


def match_sg(sg_info, first_subject, second_subjects):
    """判断 sg_info 是否与用户的选科组合匹配

    first_subject: "物理" | "历史"
    second_subjects: ["化学", "生物"] 等用户选的再选科目
    """
    if not sg_info:
        return True

    # 首选匹配
    if sg_info.startswith("首选不限"):
        pass
    elif not sg_info.startswith("首选" + first_subject):
        return False

    # 再选匹配
    if "再选不限" in sg_info:
        return True

    req = sg_info.split("再选")[1]  # 取再选后面的内容

    def _has(sub):
        return sub in second_subjects

    if "、" in req and "必选" in req:
        # "化学、生物(2科必选)" → 必须同时有
        subs = req.split("(")[0].split("、")
        return all(_has(s.strip()) for s in subs)

    if "/" in req and "选1" in req:
        # "化学/生物(2选1)" → 有其一即可
        subs = req.split("(")[0].split("/")
        return any(_has(s.strip()) for s in subs)

    if "、" in req:
        # "化学、生物" 不带必选标记 → 按至少一项算
        subs = req.split("(")[0].split("、")
        return any(_has(s.strip()) for s in subs)

    if "/" in req:
        subs = req.split("(")[0].split("/")
        return any(_has(s.strip()) for s in subs)

    # 单科 "再选化学"
    return _has(req.strip())


def get_matching_sg(first_subject, second_subjects):
    """返回匹配用户选科的所有 sg_info 值列表"""
    if not first_subject:
        return []
    conn = get_conn()
    rows = conn.execute("SELECT DISTINCT sg_info FROM scores WHERE sg_info != ''").fetchall()
    conn.close()
    return [r[0] for r in rows if match_sg(r[0], first_subject, second_subjects)]


def handle_api(path, params):
    if path == "/api/stats":
        conn = get_conn()
        r = {}
        r["total"] = conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0]
        r["schools"] = conn.execute("SELECT COUNT(DISTINCT school_name) FROM scores").fetchone()[0]
        r["majors"] = conn.execute("SELECT COUNT(DISTINCT sp_name) FROM scores").fetchone()[0]
        r["years"] = [y[0] for y in conn.execute("SELECT DISTINCT year FROM scores ORDER BY year").fetchall()]
        r["provinces"] = [p[0] for p in conn.execute("SELECT DISTINCT school_province FROM scores WHERE school_province!='' ORDER BY school_province").fetchall()]
        r["level2"] = [l[0] for l in conn.execute("SELECT DISTINCT level2_name FROM scores WHERE level2_name!='' ORDER BY level2_name").fetchall()]
        r["level3"] = [l[0] for l in conn.execute("SELECT DISTINCT level3_name FROM scores WHERE level3_name!='' ORDER BY level3_name").fetchall()]
        r["level1"] = [l[0] for l in conn.execute("SELECT DISTINCT level1_name FROM scores WHERE level1_name!='' ORDER BY level1_name").fetchall()]
        r["level1_counts"] = {row[0]: {"total": row[1], "complete": row[2]} for row in conn.execute('''
            SELECT level1_name, COUNT(*),
                   SUM(CASE WHEN level2_name!="" AND level3_name!="" AND (sp_name!="" OR spname!="") THEN 1 ELSE 0 END)
            FROM scores GROUP BY level1_name
        ''').fetchall()}
        conn.close()
        return r

    if path == "/api/schools":
        kw = params.get("q", [""])[0]
        province = params.get("province", [""])[0]
        level1 = params.get("level1", [""])[0]
        sql = "SELECT DISTINCT school_id, school_name, school_province FROM scores WHERE 1=1"
        args = []
        if kw:
            sql += " AND school_name LIKE ?"
            args.append(f"%{kw}%")
        if province:
            sql += " AND school_province=?"
            args.append(province)
        if level1:
            sql += " AND level1_name=?"
            args.append(level1)
        limit = params.get("limit", ["200"])[0]
        sql += f" ORDER BY school_name LIMIT {int(limit)}"
        return query_db(sql, args)

    if path == "/api/majors":
        level1 = params.get("level1", [""])[0]
        # 高职/职业 → 动态查库；本科 → 静态官方目录
        if level1 and ("专科" in level1 or "职业" in level1):
            tree = {}
            rows = query_db("""
                SELECT DISTINCT level2_name, level3_name,
                    CASE WHEN sp_name != '' THEN sp_name ELSE spname END as sp_name
                FROM scores
                WHERE level2_name != '' AND level3_name != '' AND (sp_name != '' OR spname != '')
                  AND level1_name=?
                ORDER BY level2_name, level3_name, sp_name
            """, [level1])
            for r in rows:
                l2, l3, sp = r["level2_name"], r["level3_name"], r["sp_name"]
                tree.setdefault(l2, {}).setdefault(l3, [])
                if sp not in tree[l2][l3]:
                    tree[l2][l3].append(sp)
            return tree
        # 本科 → 静态标准树
        return STATIC_TREE

    if path == "/api/search":
        school = params.get("school", [""])[0]
        major = [v for v in params.get("major", []) if v]
        year = params.get("year", [""])[0]
        province = params.get("province", [""])[0]
        level2 = [v for v in params.get("level2", []) if v]
        level3 = [v for v in params.get("level3", []) if v]
        level1 = params.get("level1", [""])[0]
        batch = params.get("batch", [""])[0]
        zslx = params.get("zslx", [""])[0]
        rank_from = params.get("rank_from", [""])[0]
        rank_to = params.get("rank_to", [""])[0]
        sort = params.get("sort", ["min_desc"])[0]
        page = int(params.get("page", ["1"])[0])
        size = int(params.get("size", ["50"])[0])
        first_subject = params.get("first_subject", [""])[0]
        second_subjects = [v for v in params.get("second_subjects", []) if v]
        tags = [v for v in params.get("tags", []) if v]

        sql = "SELECT * FROM scores WHERE 1=1"
        count_sql = "SELECT COUNT(*) as cnt FROM scores WHERE 1=1"
        args = []

        # 标签筛选：转成学校名列表做 IN 查询
        if tags:
            tag_schools = set()
            for name in (n["school_name"] for n in query_db("SELECT DISTINCT school_name FROM scores", [])):
                st = school_tags(name)
                if any(t in st for t in tags):
                    tag_schools.add(name)
            if tag_schools:
                ph = ",".join(["?"] * len(tag_schools))
                tag_cond = f" AND school_name IN ({ph})"
                sql += tag_cond
                count_sql += tag_cond
                args.extend(tag_schools)

        conditions = []
        if school:
            conditions.append(" AND school_name LIKE ?")
            args.append(f"%{school}%")
        if major:
            ph = " OR ".join(["sp_name LIKE ? OR spname LIKE ?"] * len(major))
            conditions.append(f" AND ({ph})")
            for m in major:
                kw = major_search_keywords(m)
                args.extend([f"%{kw}%", f"%{kw}%"])
        if year:
            conditions.append(" AND year=?")
            args.append(int(year))
        if province:
            conditions.append(" AND school_province=?")
            args.append(province)
        if level2:
            ph = ",".join(["?"] * len(level2))
            conditions.append(f" AND level2_name IN ({ph})")
            args.extend(level2)
        if level3:
            ph = " OR ".join(["sp_name LIKE ? OR spname LIKE ?"] * len(level3))
            conditions.append(f" AND ({ph})")
            for l3 in level3:
                kw = major_search_keywords(l3)
                args.extend([f"%{kw}%", f"%{kw}%"])
        if level1:
            conditions.append(" AND level1_name=?")
            args.append(level1)
        if batch:
            conditions.append(" AND local_batch_name=?")
            args.append(batch)
        if zslx:
            conditions.append(" AND zslx_name=?")
            args.append(zslx)
        if first_subject and second_subjects:
            matching_sgs = get_matching_sg(first_subject, second_subjects)
            if matching_sgs:
                ph = ",".join(["?"] * len(matching_sgs))
                conditions.append(f" AND sg_info IN ({ph})")
                args.extend(matching_sgs)
        if rank_from:
            conditions.append(f" AND ({RANK_EXPR}) >= ?")
            args.append(int(rank_from))
        if rank_to:
            conditions.append(f" AND ({RANK_EXPR}) <= ?")
            args.append(int(rank_to))
        conditions.append(" AND min IS NOT NULL AND min != ''")

        for c in conditions:
            sql += c
            count_sql += c

        # 排序
        sort_map = {
            "min_desc": "min DESC",
            "min_asc": "min ASC",
            "max_desc": "max DESC",
            "avg_desc": "average DESC",
            "section_asc": f"({RANK_EXPR}) ASC",
            "lq_desc": "lq_num DESC",
            "school": "school_name, year DESC, min DESC",
        }
        sql += f" ORDER BY {sort_map.get(sort, 'min DESC')}"
        sql += f" LIMIT {size} OFFSET {(page - 1) * size}"

        total = query_db(count_sql, args)[0]["cnt"]
        rows = query_db(sql, args)
        for r in rows:
            r["level3_name"] = r.get("level3_name", "")
            r["tags"] = school_tags(r["school_name"])
        return {"total": total, "page": page, "size": size, "rows": rows}

    if path == "/api/compare":
        # 多校/多专业对比
        schools = [v for v in params.get("schools", []) if v]
        major = [v for v in params.get("major", []) if v]
        level2 = [v for v in params.get("level2", []) if v]
        level3 = [v for v in params.get("level3", []) if v]
        year = params.get("year", [""])[0]
        level1 = params.get("level1", [""])[0]
        first_subject = params.get("first_subject", [""])[0]
        second_subjects = [v for v in params.get("second_subjects", []) if v]
        tags = [v for v in params.get("tags", []) if v]

        sql = "SELECT school_name, school_province, year, sp_name, spname, min, max, average, min_section, lq_num FROM scores WHERE 1=1"
        args = []
        if schools:
            ph = ",".join(["?"] * len(schools))
            sql += f" AND school_name IN ({ph})"
            args.extend(schools)
        if major:
            ph = " OR ".join(["sp_name LIKE ? OR spname LIKE ?"] * len(major))
            sql += f" AND ({ph})"
            for m in major:
                kw = major_search_keywords(m)
                args.extend([f"%{kw}%", f"%{kw}%"])
        elif level3:
            ph = " OR ".join(["sp_name LIKE ? OR spname LIKE ?"] * len(level3))
            sql += f" AND ({ph})"
            for l3 in level3:
                kw = major_search_keywords(l3)
                args.extend([f"%{kw}%", f"%{kw}%"])
        elif level2:
            ph = ",".join(["?"] * len(level2))
            sql += f" AND level2_name IN ({ph})"
            args.extend(level2)
        if tags:
            tag_schools = set()
            for name in (n["school_name"] for n in query_db("SELECT DISTINCT school_name FROM scores", [])):
                st = school_tags(name)
                if any(t in st for t in tags):
                    tag_schools.add(name)
            if tag_schools:
                ph = ",".join(["?"] * len(tag_schools))
                sql += f" AND school_name IN ({ph})"
                args.extend(tag_schools)
        if year:
            sql += " AND year=?"
            args.append(int(year))
        if level1:
            sql += " AND level1_name=?"
            args.append(level1)
        if first_subject and second_subjects:
            matching_sgs = get_matching_sg(first_subject, second_subjects)
            if matching_sgs:
                ph = ",".join(["?"] * len(matching_sgs))
                sql += f" AND sg_info IN ({ph})"
                args.extend(matching_sgs)
        if year:
            sql += " AND year=?"
            args.append(int(year))
        sql += " ORDER BY school_name, year DESC, min DESC"
        rows = query_db(sql, args)
        for r in rows:
            r["tags"] = school_tags(r["school_name"])
        return rows

    return {"error": "unknown endpoint"}


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            with open(os.path.join(os.path.dirname(__file__), "index.html"), "rb") as f:
                self.wfile.write(f.read())
            return

        if path.startswith("/api/"):
            result = handle_api(path, params)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode())
            return

        self.send_error(404)

    def log_message(self, format, *args):
        pass  # 静默日志


if __name__ == "__main__":
    print(f"掌上高考数据查询 http://localhost:{PORT}")
    server = http.server.HTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
