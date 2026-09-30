-- 2026-09-30：为 submission 表补两个时间索引
--
-- 背景：submission 表原本只有 id / contest_id / problem_id / result / user_id 五个索引，
-- 没有 create_time 索引，导致两个接口在 144 万行的表上做全表扫描：
--
--   /api/submissions
--     WHERE contest_id IS NULL ORDER BY create_time DESC LIMIT 12
--     → Bitmap Heap Scan on contest_id，读 46378 个 block（约 360MB）才返回 12 行，实测 4298ms
--
--   /api/admin/dashboard_info
--     WHERE create_time >= 当天零点  → count(*)
--     → Parallel Seq Scan，读 283835 个 block（约 2.2GB），实测 9615ms
--
-- 线上已执行（2026-09-30），执行后实测：前者 0.07ms、后者约 10ms。
--
-- ⚠️ CREATE INDEX CONCURRENTLY 不能在事务块里执行，psql 请逐条执行，不要用 -1 / --single-transaction。
-- ⚠️ 若中途失败会留下 INVALID 索引，先 DROP INDEX 再重来：
--      SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;

-- 供 dashboard 的「今日提交数」以及任何按 create_time 排序/过滤的查询使用（约 31MB）
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_submission_create_time
    ON submission (create_time DESC);

-- 供 /api/submissions 的「非比赛提交列表」使用：谓词与查询完全吻合，
-- 取 12 行只需走 12 个索引项；count(*) 也能走 index-only scan（约 1.4MB）
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_submission_noncontest_ctime
    ON submission (create_time DESC) WHERE contest_id IS NULL;
