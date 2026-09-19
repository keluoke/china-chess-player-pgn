#!/usr/bin/env python3
"""Publish an honest technical baseline and fixed evaluation set, never invented traffic."""
import argparse, datetime as dt, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--platform-export',type=Path);a=p.parse_args()
 m=json.loads((ROOT/'docs/data/seo/manifest.json').read_text())
 names=['侯逸凡','居文君','丁立人','韦奕','许翔宇']
 queries=[{'category':'player','query':name+suffix} for name in names for suffix in ['棋谱下载',' FIDE 等级分']]
 queries += [{'category':'ranking','query':'中国 '+group+' '+control+' 等级分榜'} for group in ['U12','U18','成年组'] for control in ['标准棋','快棋']]
 queries += [{'category':'master-series','query':q} for q in ['2026 棋协大师赛各站棋谱','2025 棋协大师赛各站成绩','2024 棋协大师赛棋谱下载','2023 棋协大师赛赛站','2022 棋协大师赛成绩','棋协大师赛公开组棋谱','棋协大师赛绍兴站棋谱','棋协大师赛盐城站成绩']]
 queries += [{'category':'methodology','query':q} for q in ['成绩完整为什么没有棋谱','公开直播范围完整和全台棋谱完整的区别','如何下载中国棋手 PGN','FIDE 标准棋和快棋等级分的区别','ChessDB 独立棋局数怎么统计','ChessDB 数据许可与更新日期']]
 platforms=json.loads(a.platform_export.read_text()) if a.platform_export else None
 report={'recordedAt':dt.datetime.now(dt.timezone.utc).isoformat(),'snapshotId':m['snapshotId'],'technicalBaseline':{'canonicalOrigin':m['origin'],'indexablePages':len(m['pages']),'playerPages':len(m['routes']['players']),'eventPages':len(m['routes']['events']),'namePages':len(m['routes'].get('names',{}))},'platformMetrics':platforms,'platformMetricsStatus':'maintainer-provided-export' if platforms is not None else 'not-connected-no-measurement','queryBankVersion':'2026-09-18-v1','queries':queries,'evaluationFields':['product','mode','language','region','checkedAt','query','citedURL','factsCorrect','notes'],'notice':'技术发布通过不等于已收录；引用数不等于点击；无平台数据不得填零或推算增长。'}
 a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report['technicalBaseline'],ensure_ascii=False))
if __name__=='__main__':main()
