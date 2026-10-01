#!/bin/bash
#=============================================================================
# XMUOJ Auto-Tagging Batch Script
# Processes 20 problems per run, matching titles/descriptions against
# keyword→tag mappings and inserting into problem_tags table.
#
# Usage: ./auto_tag.sh [--dry-run]
# Cron:  */10 * * * * cd /home/ubuntu/xmuoj/scripts && ./auto_tag.sh >> tag_cron.log 2>&1
#=============================================================================

set -euo pipefail

STATE_FILE="/home/ubuntu/xmuoj/scripts/tag_state.txt"
LOG_FILE="/home/ubuntu/xmuoj/scripts/tag_log.txt"
BATCH_SIZE=20
DRY_RUN=false

[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

# Helper: run a SQL query and return result (single value or multi-line)
db_query() {
    # Accept SQL via stdin, pass to docker exec -i
    sudo docker exec -i oj-postgres psql -U onlinejudge -d onlinejudge -t -A -q
}

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# ─── State Management ───────────────────────────────────────────────────────

LAST_ID=$(cat "$STATE_FILE" 2>/dev/null || echo "0")
LAST_ID=$((LAST_ID))

# Get max problem ID for bounds checking
MAX_ID=$(echo "SELECT COALESCE(MAX(id), 0) FROM problem WHERE visible = true;" | db_query)
MAX_ID=$((MAX_ID))

if [[ $LAST_ID -ge $MAX_ID ]]; then
    log "All visible problems processed (last=$LAST_ID, max=$MAX_ID). Resetting to 0."
    LAST_ID=0
fi

log "Starting batch: last_id=$LAST_ID, max_id=$MAX_ID, dry_run=$DRY_RUN"

# ─── Fetch Batch ────────────────────────────────────────────────────────────

BATCH_IDS=$(echo "
SELECT array_agg(id ORDER BY id)::text
FROM (
    SELECT id
    FROM problem
    WHERE visible = true
      AND id > $LAST_ID
      AND (
          id NOT IN (SELECT DISTINCT problem_id FROM problem_tags)
          OR id IN (
              SELECT problem_id FROM problem_tags
              GROUP BY problem_id HAVING count(*) < 2
          )
      )
    ORDER BY id
    LIMIT $BATCH_SIZE
) sub;
" | db_query)

# Strip braces
BATCH_IDS="${BATCH_IDS#\{}"
BATCH_IDS="${BATCH_IDS%\}}"

if [[ -z "$BATCH_IDS" || "$BATCH_IDS" == "NULL" ]]; then
    log "No more problems needing tags after id=$LAST_ID."
    echo "$MAX_ID" > "$STATE_FILE"
    exit 0
fi

# Get the new last ID (max of this batch)
NEW_LAST_ID=$(echo "$BATCH_IDS" | tr ',' '\n' | sort -n | tail -1)
[[ -z "$NEW_LAST_ID" ]] && NEW_LAST_ID=$LAST_ID

# Count
BATCH_COUNT=$(echo "$BATCH_IDS" | tr ',' '\n' | wc -l)
log "Batch: $BATCH_COUNT problems, IDs: $BATCH_IDS"

# ─── Load Tag IDs ───────────────────────────────────────────────────────────

declare -A TAG_IDS

while IFS='|' read -r tag_name tag_id; do
    [[ -n "$tag_name" ]] && TAG_IDS["$tag_name"]="$tag_id"
done < <(echo "SELECT name, id FROM problem_tag WHERE is_active = true;" | db_query)

log "Loaded ${#TAG_IDS[@]} active tags from DB"

# ─── Keyword → Tag Mapping ─────────────────────────────────────────────────
#
# Format: "keyword_regex" => "tag_name1,tag_name2,..."
#   keyword_regex uses PostgreSQL ~* (case-insensitive regex match)
#   Multiple keywords separated by |

RULES=(
    # ─── Prefix Sum & Difference ───────────────────────────────────────
    "前缀和|子段和|子数组和"                        "前缀和与差分"
    "二维前缀和"                                    "二维前缀和"
    "差分(?!约束)|区间加"                           "差分"
    "二维差分"                                      "二维差分"

    # ─── Binary Search ─────────────────────────────────────────────────
    "二分(?!图|法)"                                 "二分"

    # ─── Two Pointers ─────────────────────────────────────────────────
    "双指针|滑动窗口"                                 "双指针"

    # ─── Greedy ────────────────────────────────────────────────────────
    "贪心|区间选点|不相交区间|区间覆盖|区间合并|区间分组" "贪心"
    "推公式|排序不等式|绝对值不等式"                    "推公式"

    # ─── Math ──────────────────────────────────────────────────────────
    "快速幂|模幂|矩阵快速幂"                          "快速幂"
    "质数|素数|筛质数|质因数|素数筛"                   "质数"
    "试除法"                                         "试除法"
    "筛法"                                           "筛法"
    "约数|GCD|LCM|公约数|最大公约数|公倍数"            "约数"
    "欧拉函数|欧拉定理"                               "欧拉函数"
    "求组合数|组合数|排列组合"                         "组合数"
    "容斥原理|容斥"                                   "容斥原理"
    "高斯消元|高斯消去"                               "高斯消元"
    "博弈论|NIM|石子游戏|必胜策略"                     "博弈论"
    "扩展欧几里得|exgcd|裴蜀定理"                     "扩展欧几里得"
    "逆元|模逆|费马小定理"                             "费马小定理"
    "Lucas定理|lucas"                                "Lucas定理"
    "卡特兰数|Catalan"                               "卡特兰数"

    # ─── DFS / BFS ─────────────────────────────────────────────────────
    "DFS|深度优先搜索|回溯.*搜索|全排列|皇后|N皇后|数独" "DFS"
    "BFS|广度优先搜索|迷宫.*搜索|八数码"                "BFS"
    "FloodFill|flood.fill|池塘计数"                    "FloodFill"
    "记忆化搜索|记忆化"                                "记忆化搜索"
    "剪枝|搜索剪枝|搜索优化"                           "剪枝"
    "迭代加深|IDA\*"                                  "迭代加深"
    "A\*算法|A星算法|A\*搜索|A星搜索"                 "A*"

    # ─── Data Structures ───────────────────────────────────────────────
    "并查集|合并集合|连通块|食物链|连通分量"            "并查集"
    "带权并查集"                                      "带权并查集"
    "堆|堆排序"                                       "堆"
    "优先队列|priority.queue"                         "优先队列"
    "单调栈"                                          "单调栈"
    "单调队列|滑动窗口最值"                             "单调队列"
    "Trie|trie|字符串统计|字典树"                      "Trie"
    "树状数组|Fenwick|fenwick|BIT索引"                 "树状数组"
    "线段树|区间查询|区间修改|区间和"                   "线段树"
    "栈(?!列)"                                        "栈"
    "队列"                                            "队列"
    "链表"                                            "链表"
    "哈希表|哈希|散列"                                 "哈希表"
    "STL|stl"                                         "STL"
    "平衡树|Treap|treap|Splay|splay"                  "平衡树"

    # ─── DP ────────────────────────────────────────────────────────────
    "背包|01背包|完全背包|多重背包|分组背包"            "背包问题"
    "多重背包"                                        "多重背包"
    "线性DP|LIS|LCS|编辑距离|数字三角|数字三角形|最长上升|最长公共" "线性DP"
    "数字三角形"                                      "数字三角形DP"
    "区间DP|石子合并|矩阵链"                           "区间DP"
    "状态压缩DP|状压DP|蒙德里安"                       "状态压缩DP"
    "状态压缩|状压"                                    "状态压缩"
    "状态机DP|状态机"                                  "状态机DP"
    "树形DP|树形dp|舞会|树的最长路径"                   "树形DP"
    "数位DP|数位dp|度的数量|Windy|windy"               "数位DP"
    "计数类DP|计数DP|整数划分"                          "计数类DP"
    "斜率DP|斜率优化"                                  "斜率DP"
    "单调队列优化DP"                                   "单调队列优化DP"

    # ─── Graph Theory ──────────────────────────────────────────────────
    "Dijkstra|dijkstra|单源最短路.*热浪"               "Dijkstra"
    "SPFA|spfa|Bellman.Ford|bellman|道路与航线|负权"   "SPFA"
    "Floyd|floyd|多源最短路"                           "Floyd"
    "单源最短路"                                       "单源最短路"
    "最短路(?!.*扩展)"                                  "最短路"
    "Prim|prim|Kruskal|kruskal|最小生成树|局域网"      "最小生成树"
    "最小生成树的扩展"                                  "最小生成树的扩展应用"
    "拓扑排序|拓扑序列|家谱树"                          "拓扑排序"
    "二分图|染色法|匈牙利|匹配(?!.*字符串)"             "二分图"
    "染色法"                                           "染色法"
    "欧拉路径|欧拉回路|Hierholzer|欧拉图"              "欧拉路径"
    "差分约束"                                         "差分约束"
    "最近公共祖先|LCA"                                 "最近公共祖先"
    "强连通分量|SCC|Tarjan.*连通"                       "强连通分量"
    "Tarjan|tarjan"                                    "Tarjan算法"
    "割点|割边|双连通"                                  "割点"
    "网络流|最大流|最小割|Dinic|dinic"                  "网络流"
    "有向图的强连通分量"                                 "有向图的强连通分量"
    "无向图的双连通分量"                                 "无向图的双连通分量"

    # ─── Sorting / Basic ───────────────────────────────────────────────
    "快速排序|快排"                                    "快速排序"
    "归并排序|归并"                                    "归并排序"
    "高精度|大整数|大数加减|大数乘法"                    "高精度"
    "位运算|位操作|lowbit"                              "位运算"
    "离散化|坐标压缩"                                   "离散化"
    "进制转换|进制"                                     "进制转换"
    "逆序对"                                           "逆序对"

    # ─── Geometry ──────────────────────────────────────────────────────
    "计算几何|凸包|叉积"                                "计算几何"

    # ─── String ────────────────────────────────────────────────────────
    "KMP|kmp|字符串匹配|模式匹配"                        "KMP"
    "AC自动机|ac自动机|多模式匹配"                       "AC自动机"

    # ─── Advanced ──────────────────────────────────────────────────────
    "树的直径(?!.*DP)"                                  "树的直径"
    "分治(?!.*点)"                                      "分治"
    "模拟退火"                                          "模拟退火"
    "Huffman|huffman|哈夫曼"                            "Huffman树"
    "模拟(?!.*退火|.*栈|.*队列)"                         "模拟"
)

# ─── Execute Matching ──────────────────────────────────────────────────────

total_inserted=0

for ((i=0; i<${#RULES[@]}; i+=2)); do
    pattern="${RULES[$i]}"
    tag_names="${RULES[$i+1]}"

    # Split comma-separated tag names
    IFS=',' read -ra NAMES <<< "$tag_names"

    for tag_name in "${NAMES[@]}"; do
        # Trim whitespace
        tag_name=$(echo "$tag_name" | xargs)
        tag_id="${TAG_IDS[$tag_name]:-}"

        if [[ -z "$tag_id" ]]; then
            log "WARNING: Tag '$tag_name' not found in DB, skipping pattern: $pattern"
            continue
        fi

        if $DRY_RUN; then
            match_count=$(echo "
SELECT count(*)
FROM problem
WHERE id = ANY(ARRAY[$BATCH_IDS])
  AND (title ~* '($pattern)' OR description ~* '($pattern)')
  AND id NOT IN (SELECT problem_id FROM problem_tags WHERE problemtag_id = $tag_id);
" | db_query)
            match_count=$((match_count))
            if [[ $match_count -gt 0 ]]; then
                log "DRY-RUN: [$tag_name] pattern='$pattern' → $match_count matches in batch"
            fi
        else
            result=$(echo "
INSERT INTO problem_tags (problem_id, problemtag_id)
SELECT id, $tag_id
FROM problem
WHERE id = ANY(ARRAY[$BATCH_IDS])
  AND (title ~* '($pattern)' OR description ~* '($pattern)')
  AND id NOT IN (SELECT problem_id FROM problem_tags WHERE problemtag_id = $tag_id)
ON CONFLICT DO NOTHING
RETURNING problem_id;
" | db_query)

            # Count inserted rows
            inserted=$(echo "$result" | grep -c '^[0-9]' || true)
            if [[ $inserted -gt 0 ]]; then
                inserted=$((inserted))
                total_inserted=$((total_inserted + inserted))
                log "  +$inserted [$tag_name] ← pattern='$pattern'"
            fi
        fi
    done
done

# ─── Update State ──────────────────────────────────────────────────────────

if $DRY_RUN; then
    log "DRY-RUN complete. Would update state to $NEW_LAST_ID"
else
    echo "$NEW_LAST_ID" > "$STATE_FILE"
    log "Batch complete: $total_inserted tags inserted, state updated to $NEW_LAST_ID"
fi

# ─── Progress Summary ──────────────────────────────────────────────────────

REMAINING_0=$(echo "SELECT count(*) FROM problem WHERE visible = true AND id NOT IN (SELECT DISTINCT problem_id FROM problem_tags) AND id > $NEW_LAST_ID;" | db_query)
REMAINING_1=$(echo "SELECT count(*) FROM problem WHERE visible = true AND id IN (SELECT problem_id FROM problem_tags GROUP BY problem_id HAVING count(*) = 1) AND id > $NEW_LAST_ID;" | db_query)
REMAINING_0=$((REMAINING_0))
REMAINING_1=$((REMAINING_1))

log "Remaining after this batch: $REMAINING_0 with 0 tags, $REMAINING_1 with 1 tag"

if [[ $NEW_LAST_ID -ge $MAX_ID ]]; then
    log "=== ALL VISIBLE PROBLEMS PROCESSED ==="
fi
