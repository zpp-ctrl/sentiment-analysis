-- ============================================================
-- 品品每日自动财经舆情预测任务 - MySQL建表语句
-- 数据库名: financial_sentiment
-- 使用方法: mysql -u root -p < sql/init_db.sql
-- ============================================================

CREATE DATABASE IF NOT EXISTS financial_sentiment
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE financial_sentiment;

-- -----------------------------------------------------------
-- 表1: 原始帖子表 (raw_posts)
-- 存储抖音、小红书采集的每一条原始帖子数据
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw_posts (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',
    post_id         VARCHAR(128)    NOT NULL COMMENT '平台帖子唯一ID',
    platform        VARCHAR(32)     NOT NULL COMMENT '平台来源: douyin / xiaohongshu',
    account_id      VARCHAR(64)     NOT NULL COMMENT '博主账号ID',
    account_name    VARCHAR(256)    NOT NULL COMMENT '博主昵称',
    account_level   VARCHAR(32)     NOT NULL COMMENT '账号等级: top1000 / 10w_plus',
    post_type       VARCHAR(32)     NOT NULL COMMENT '帖子类型: video / image_text',
    post_content    TEXT            NOT NULL COMMENT '帖子正文/短视频文案',
    post_url        VARCHAR(512)    DEFAULT NULL COMMENT '帖子链接',
    like_count      INT             DEFAULT 0 COMMENT '点赞数',
    comment_count   INT             DEFAULT 0 COMMENT '评论数',
    share_count     INT             DEFAULT 0 COMMENT '转发/分享数',
    post_time       DATETIME        NOT NULL COMMENT '帖子发布时间',
    collect_time    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '采集入库时间',
    is_ad           TINYINT(1)      DEFAULT 0 COMMENT '是否广告/理财广告: 0=否, 1=是',
    is_stock_only   TINYINT(1)      DEFAULT 0 COMMENT '是否纯个股推荐: 0=否, 1=是',
    sentiment       VARCHAR(16)     DEFAULT NULL COMMENT '情感分类: bullish / bearish / neutral',
    sentiment_score DECIMAL(5,4)    DEFAULT NULL COMMENT '情感得分: 0~1之间, 越高越偏多',
    UNIQUE KEY uk_post (post_id, platform),
    INDEX idx_platform (platform),
    INDEX idx_account_level (account_level),
    INDEX idx_post_time (post_time),
    INDEX idx_collect_time (collect_time),
    INDEX idx_sentiment (sentiment)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='原始帖子表 - 每日增量追加';


-- -----------------------------------------------------------
-- 表2: 每日情绪统计表 (daily_sentiment_stats)
-- 按日期汇总看多/看空/中性占比，区分头部账号与普通博主
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_sentiment_stats (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',
    stat_date           DATE            NOT NULL COMMENT '统计日期',
    account_level       VARCHAR(32)     NOT NULL COMMENT '账号等级: all / top1000 / 10w_plus',
    total_posts         INT             DEFAULT 0 COMMENT '有效帖子总数(剔除广告等)',
    bullish_count       INT             DEFAULT 0 COMMENT '看多帖子数',
    bearish_count       INT             DEFAULT 0 COMMENT '看空帖子数',
    neutral_count       INT             DEFAULT 0 COMMENT '中性帖子数',
    bullish_ratio       DECIMAL(6,4)    DEFAULT 0.0000 COMMENT '看多占比',
    bearish_ratio       DECIMAL(6,4)    DEFAULT 0.0000 COMMENT '看空占比',
    neutral_ratio       DECIMAL(6,4)    DEFAULT 0.0000 COMMENT '中性占比',
    avg_sentiment_score DECIMAL(5,4)    DEFAULT 0.0000 COMMENT '平均情感得分',
    create_time         DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '统计入库时间',
    UNIQUE KEY uk_date_level (stat_date, account_level),
    INDEX idx_stat_date (stat_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='每日情绪统计表 - 按日期维度增量写入';


-- -----------------------------------------------------------
-- 表3: 预测回测记录表 (prediction_backtest_records)
-- 存储每日情感分析预测结果及次日收盘回测比对数据
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS prediction_backtest_records (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',
    predict_date        DATE            NOT NULL COMMENT '预测生成日期(T日)',
    target_date         DATE            NOT NULL COMMENT '预测目标日期(T+1日)',
    index_code          VARCHAR(32)     NOT NULL COMMENT '指数代码: 000001.SH / 000300.SH / 000905.SH / 000852.SH',
    index_name          VARCHAR(64)     NOT NULL COMMENT '指数名称: 上证指数 / 沪深300 / 中证500 / 中证1000',
    predict_direction   VARCHAR(16)     NOT NULL COMMENT '预测方向: up / down',
    predict_prob        DECIMAL(6,4)    NOT NULL COMMENT '预测上涨概率(0~1)',
    actual_direction    VARCHAR(16)     DEFAULT NULL COMMENT '实际方向: up / down (T+1收盘后回填)',
    actual_pct_change   DECIMAL(8,4)    DEFAULT NULL COMMENT '实际涨跌幅% (T+1收盘后回填)',
    is_correct          TINYINT(1)      DEFAULT NULL COMMENT '预测是否正确: 1=正确, 0=错误 (T+1收盘后回填)',
    backtest_time       DATETIME        DEFAULT NULL COMMENT '回测更新时间',
    create_time         DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '预测记录创建时间',
    UNIQUE KEY uk_predict (predict_date, index_code),
    INDEX idx_predict_date (predict_date),
    INDEX idx_target_date (target_date),
    INDEX idx_index_code (index_code),
    INDEX idx_is_correct (is_correct)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='预测回测记录表 - 基于情感分析的涨跌预测+回测比对';