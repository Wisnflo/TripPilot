"""One evidence-derived report for all stages; missing runs stay explicitly missing."""
import argparse
import json
from pathlib import Path
import re
import sys
from statistics_tools import paired

ROOT=Path(__file__).resolve().parents[1]
LABELS={'structured':'TripPilot','b2':'ReAct + 检查器','b1':'ReAct','b0':'单次生成',
        'patch':'增量修复','rules':'确定性修复','no_feedback':'关闭反馈','direct_plan':'完整 JSON 生成'}


def number(value,digits=2):return '未知' if value is None else f'{value:,.{digits}f}'
def percent(value):return '未知' if value is None else f'{100*value:.1f}%'
def bounds(value):return '未知' if not value else '–'.join(percent(v) for v in value)
def money(value):return '未知' if value.get('upper') is None else number(value['upper'],6)


def table(report):
    lines=['| 方法 | 全部通过 | 可用且通过 | 格式通过 | 常识通过 | 硬条件通过 | 已返回 token | p50 秒 | p95 秒 | 已知用量标价（元） | 每成功任务已知标价（元） |',
           '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for method,r in report['summary'].items():
        values=[LABELS.get(method,method),f"{r['success']}/{r['tasks']}",str(r['usable']),str(r['schema']),str(r['commonsense']),str(r['hard']),
                f"{r['tokens']:,}",number(r['p50']),number(r['p95']),money(r['known_usage_cost_CNY']),money(r['known_usage_cost_per_success_CNY'])]
        lines.append('| '+' | '.join(values)+' |')
    return '\n'.join(lines)


def render(root,prefix):
    root=Path(root);pipeline=root/'experiments/v3'/(prefix+'-pipeline')
    state=json.loads((pipeline/'state.json').read_text()) if (pipeline/'state.json').exists() else {}
    reports={}
    for stage in ('static','components','repeat-1','repeat-2','dynamic','second-model'):
        path=root/'experiments/v3'/(prefix+'-'+stage)/'analysis.json'
        if path.exists():reports[stage]=json.loads(path.read_text())
    lines=['# TripPilot 综合评测报告','',f"评测标识：`{prefix}`。已产生分析的阶段：{len(reports)}/6。",'',
           '## 结论','']
    initial=reports.get('static');dynamic=reports.get('dynamic')
    if initial and {'structured','b2'}<=initial['summary'].keys():
        left=initial['summary']['structured'];right=initial['summary']['b2'];delta=left['success']-right['success']
        judgement='更多' if delta>0 else '相同' if delta==0 else '更少'
        lines.append(f"首次规划中，TripPilot 完整通过 {left['success']}/{left['tasks']}，ReAct + 检查器通过 {right['success']}/{right['tasks']}，通过数{judgement}。")
        stats=initial['paired_statistics'].get('structured',{})
        lines.append(f"已返回 token 减少 {percent(stats.get('token_reduction'))}，累计任务耗时减少 {percent(stats.get('time_reduction'))}。减少比例为负值表示用量或耗时增加。")
        lines.append(f"父任务配对的精确 McNemar p={number(stats.get('exact_mcnemar_p'),6)}；结论范围为本协议的任务、模型服务和执行预算。")
    else:lines.append('首次规划完整对照尚未产生结果，不能判断当前实现是否提升。')
    if dynamic:
        r=dynamic['summary'];lines.append('动态修复从相同有效种子出发：'+ '；'.join(f"{LABELS.get(m,m)} {s['success']}/{s['tasks']}" for m,s in r.items())+'。这衡量条件修复，不代表端到端成功率。')
    else:lines.append('独立可行动态任务上的完整修复对照尚未产生结果。')
    lines+=['','## 方法与共同执行条件','',
            '单次生成仅调用一次模型；ReAct 使用公开工具；带检查器的 ReAct 接收校验反馈。TripPilot 将需求和候选引用转为行程，由程序复制事实、连接交通并计算费用。所有首次规划方法获得相同公开候选信息包，缺失交通均使用相同事实补齐。',
            '', '每任务最多 16 次逻辑模型调用、500,000 个已返回 token、400 次后端工具查询和 4 次反馈机会；单次输出最多 10,000 token。首次规划限时 360 秒，动态修复限时 240 秒，请求限时 180 秒，最多尝试两次，并发四项。程序准备、验证和模型回退均计入任务预算。',
            '', '输入 token 使用保守预估，已返回用量按服务记录累计；失败或中断请求的远端用量可能未知。表中金额为已知用量的服务标价估算，无法替代实际账单。',
            '', '## 首次规划','']
    if initial:
        lines+=[f"模型服务标识：`{initial['model']}`。",'',table(initial),'',
                '| 方法 | easy | medium | human |','| --- | ---: | ---: | ---: |']
        for method,groups in initial['strata'].items():
            lines.append('| '+LABELS.get(method,method)+' | '+' | '.join(f"{groups.get(k,{}).get('success',0)}/{groups.get(k,{}).get('tasks',0)}" for k in ('easy','medium','human'))+' |')
        lines+=['','相同父任务配对的 95% 区间：']
        for method,stats in initial['paired_statistics'].items():
            if stats.get('parents'):
                interval=stats['parent_bootstrap_95'];lines.append(f"- {LABELS.get(method,method)} 相对 ReAct + 检查器：通过率差 {bounds(interval['success_rate_difference'])}；token 减少 {bounds(interval['token_reduction'])}；累计耗时减少 {bounds(interval['time_reduction'])}。")
    else:lines.append('未执行完毕。')
    lines+=['','## 组件实验','']
    if 'components' in reports and initial:
        component=reports['components'];uids={r['uid'] for r in component['rows']}
        rows=component['rows']+[r for r in initial['rows'] if r['method']=='structured' and r['uid'] in uids]
        lines+=['固定相同父任务与公开信息，比较关闭反馈、完整 JSON 生成和完整 TripPilot。','',
                '| 组件设置 | 全部通过 | 相对完整方法的通过率差 | 配对 95% 区间 |','| --- | ---: | ---: | ---: |']
        for method in ('structured','no_feedback','direct_plan'):
            selected=[r for r in rows if r['method']==method];stats=paired(rows,method,'structured') if method!='structured' else None
            lines.append('| '+LABELS[method]+f" | {sum(r['success'] for r in selected)}/{len(selected)} | "+(percent(stats.get('success_rate_difference')) if stats else '参照')+' | '+(bounds(stats.get('parent_bootstrap_95',{}).get('success_rate_difference')) if stats else '—')+' |')
    else:lines.append('未执行完毕。')
    lines+=['','## 重复运行与第二模型','', '| 对照 | 模型 | 方法 | 全部通过 |','| --- | --- | --- | ---: |']
    for stage in ('repeat-1','repeat-2','second-model'):
        report=reports.get(stage)
        if not report:lines.append('| '+stage+' | 未执行完毕 | — | — |');continue
        for method,s in report['summary'].items():lines.append(f"| {stage} | {report['model']} | {LABELS.get(method,method)} | {s['success']}/{s['tasks']} |")
    lines+=['','重复运行使用预先选择的相同父任务；重复次数不增加独立父任务数。第二模型使用固定的 12 个父任务，单独报告。',
            '', '## 动态变更','']
    cohort=pipeline/'dynamic.json'
    if cohort.exists():
        d=json.loads(cohort.read_text());lines.append(f"独立构造器保留了 {len(d['dynamic'])} 个具有可行参考解的事件，涉及 {d['dynamic_parent_count']} 个父任务；目标缺额为 `{json.dumps(d['shortfall'],ensure_ascii=False)}`。参考解由离线评分验证，不传给执行 Agent。")
    if dynamic:lines+=['',table(dynamic)]
    else:lines.append('修复对照未执行完毕。')
    lines+=['','## 实体、保留与失败分析','',
            '实体指标统计公开双语地点表与交通 JSON、JSONL 中无法匹配的名称或 ID，并另列缺失标识。保留指标按活动完整内容和所在天数匹配，避免删除活动后索引偏移造成误判。两者均须结合提交率阅读。','',
            '| 阶段 | 方法 | 已提交计划 | 不匹配实体数 | 缺失标识数 | 未受影响活动保留 | 回退任务数 | 可能未知计费的任务 |','| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for stage,report in reports.items():
        for method,s in report['summary'].items():
            c=s['changes'];lines.append(f"| {stage} | {LABELS.get(method,method)} | {s['submitted_plans']}/{s['tasks']} | {s['unmatched_entity_mentions']}/{s['entity_mentions']} | {s['missing_entity_mentions']} | {c['unaffected_kept']}/{c['unaffected_total']} | {s['fallback_tasks']} | {s['unknown_billing_tasks']} |")
    lines+=['','失败保留在分母；下列终态反映执行结果，不能全部归因于规划能力。','']
    for stage,report in reports.items():
        for method,s in report['summary'].items():
            lines.append(f"- {stage} / {LABELS.get(method,method)}：`{json.dumps(s['failure_states'],ensure_ascii=False)}`；离线判分异常 `{json.dumps(s['evaluation_errors'],ensure_ascii=False)}`。")
    lines+=['','共同通过的任务另行比较资源，避免把提前失败当作节省：','',
            '| 阶段 | 方法（相对 ReAct + 检查器） | 共同通过数 | 方法 token | 对照 token | 方法累计秒 | 对照累计秒 | 方法改动数 | 对照改动数 |',
            '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for stage,report in reports.items():
        for method,pair in report['paired_successes'].items():
            lines.append(f"| {stage} | {LABELS.get(method,method)} | {pair['both_successful']} | {pair['method_tokens']} | {pair['react_tokens']} | {number(pair['method_seconds'])} | {number(pair['react_seconds'])} | {pair['method_edits']} | {pair['react_edits']} |")
    lines+=['','失败示例按记录顺序取每阶段、每方法的首个失败，便于定位原始证据：','',
            '| 阶段 | 方法 | 父任务 / 事件 | 执行终态 | 格式 / 常识 / 硬条件 |','| --- | --- | --- | --- | --- |']
    for stage,report in reports.items():
        for method in report['summary']:
            row=next((r for r in report['rows'] if r['method']==method and not r['success']),None)
            if row:lines.append(f"| {stage} | {LABELS.get(method,method)} | {row['key']} | {row['terminal']} | {row['schema']} / {row['commonsense']} / {row['hard']} |")
    lines+=['','## 证据与适用范围','',
            '逐任务结果、请求尝试及执行协议保存在对应运行目录。所有阶段由相同运行协议驱动；中断请求保留原始记录，恢复不会自动购买一次新尝试。代码、任务或工具数据变化后，必须使用新的运行标识。',
            '', '数据来自 ChinaTravel 配套旅行环境，覆盖有限城市，不代表实时服务。候选搜索有界；失败不等于证明无解。动态事件通过独立构造器证明至少存在一个可行计划，但其构造分布也限制了结论的推广范围。',
            '', '最终执行状态以流水线 `state.json` 与各阶段记录共同确认。','']
    return '\n'.join(lines)


def main():
    p=argparse.ArgumentParser();p.add_argument('--prefix',required=True);p.add_argument('--output');a=p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,60}',a.prefix):p.error('invalid prefix')
    output=Path(a.output) if a.output else ROOT/'experiments/v3'/(a.prefix+'-pipeline')/'report.md'
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(render(ROOT,a.prefix))
    print('REPORT_WRITTEN',output)


if __name__=='__main__':main()
