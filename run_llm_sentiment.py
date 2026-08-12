# -*- coding: utf-8 -*-
"""LLM情感分析 - DeepSeek API"""
import sys
sys.path.insert(0, '.')
import pandas as pd
from datetime import datetime
from config import OUTPUT_DIR, LLM_BATCH_SIZE, LLM_CONCURRENCY
from llm_sentiment import classify_with_llm
from module2_text_sentiment import classify_sentiment
import os

print('=' * 60)
print('  LLM Sentiment Analysis via DeepSeek API')
print('=' * 60)

# Load data
df = pd.read_csv('output/douyin_posts_resume.csv')
print(f'\nLoaded: {len(df)} posts')

# Sample for speed (take most recent posts + random sample)
# Sort by collect_time if available
if 'collect_time' in df.columns:
    df = df.sort_values('collect_time', ascending=False)

# Take 500 posts for LLM analysis (good sample size, manageable cost)
sample_size = min(500, len(df))
df_sample = df.head(sample_size)
print(f'Sample: {sample_size} posts for LLM analysis')
print(f'Batch size: {LLM_BATCH_SIZE}, Concurrency: {LLM_CONCURRENCY}')

# ==========================================
# 1. Dictionary-based sentiment (for comparison)
# ==========================================
print('\n>>> Step 1: Dictionary-based sentiment...')
dict_sentiments = []
for text in df_sample['post_content'].fillna(''):
    label, score = classify_sentiment(str(text))
    dict_sentiments.append(label)

df_sample['dict_sentiment'] = dict_sentiments
d_bull = sum(1 for s in dict_sentiments if s == 'bullish')
d_bear = sum(1 for s in dict_sentiments if s == 'bearish')
d_neut = sum(1 for s in dict_sentiments if s == 'neutral')
print(f'  Dict:  Bullish={d_bull} ({d_bull/sample_size*100:.1f}%)')
print(f'         Bearish={d_bear} ({d_bear/sample_size*100:.1f}%)')
print(f'         Neutral={d_neut} ({d_neut/sample_size*100:.1f}%)')

# ==========================================
# 2. LLM-based sentiment
# ==========================================
print(f'\n>>> Step 2: LLM sentiment analysis ({sample_size} posts)...')
df_result = classify_with_llm(
    df_sample,
    content_col='post_content',
    batch_size=LLM_BATCH_SIZE,
    concurrency=LLM_CONCURRENCY,
)

# ==========================================
# 3. Results
# ==========================================
llm_bull = sum(1 for s in df_result['sentiment'] if s == 'bullish')
llm_bear = sum(1 for s in df_result['sentiment'] if s == 'bearish')
llm_neut = sum(1 for s in df_result['sentiment'] if s == 'neutral')
avg_conf = df_result['llm_confidence'].mean()
avg_score = df_result['sentiment_score'].mean()

print()
print('=' * 60)
print('  RESULTS COMPARISON')
print('=' * 60)
print(f'  {"":20} {"Dictionary":>12} {"LLM (DeepSeek)":>16}')
print(f'  {"-"*48}')
print(f'  {"Bullish":20} {d_bull:>5} ({d_bull/sample_size*100:5.1f}%)   {llm_bull:>5} ({llm_bull/sample_size*100:5.1f}%)')
print(f'  {"Bearish":20} {d_bear:>5} ({d_bear/sample_size*100:5.1f}%)   {llm_bear:>5} ({llm_bear/sample_size*100:5.1f}%)')
print(f'  {"Neutral":20} {d_neut:>5} ({d_neut/sample_size*100:5.1f}%)   {llm_neut:>5} ({llm_neut/sample_size*100:5.1f}%)')
print(f'  {"-"*48}')

d_ratio = d_bull/d_bear if d_bear > 0 else float('inf')
l_ratio = llm_bull/llm_bear if llm_bear > 0 else float('inf')
print(f'  {"Bull/Bear Ratio":20} {d_ratio:>12.2f}   {l_ratio:>16.2f}')
print(f'  {"Avg Score":20} {"-":>12}   {avg_score:>16.3f}')
print(f'  {"Avg Confidence":20} {"-":>12}   {avg_conf:>16.3f}')
print()

# Show some example differences
print('=' * 60)
print('  LLM vs Dictionary: Example Differences')
print('=' * 60)
diff = df_result[df_result['sentiment'] != df_result['dict_sentiment']]
print(f'  LLM disagreed with dictionary on {len(diff)}/{sample_size} posts')
print()
for i, (_, row) in enumerate(diff.head(10).iterrows()):
    content = str(row['post_content'])[:80]
    print(f'  [{i+1}] Dict={row["dict_sentiment"]} → LLM={row["sentiment"]} (conf={row["llm_confidence"]:.2f})')
    print(f'      "{content}..."')
    print(f'      Reason: {str(row["llm_reasoning"])[:100]}')
    print()

# Save results
csv_path = os.path.join(OUTPUT_DIR, f'{datetime.now().strftime("%Y%m%d")}_LLM_sentiment_sample.csv')
df_result.to_csv(csv_path, index=False, encoding='utf-8-sig')
print(f'Saved: {csv_path}')
print('DONE')
