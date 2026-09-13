# 本地等级分实验

本目录保存维护者既有、未进入 Git 的实验脚本、测试及报告。2026-09-13 从生产
Scripts/tests 与 docs 移入，文件正文保持不变；未将这些实验材料自动纳入版本控制。
原 data/manual/event-time-controls.csv 保持原位，不改人工输入。

在代码工作区运行：

```sh
python3 experiments/estimated-ratings/run.py pilot --help
python3 experiments/estimated-ratings/run.py experiments --help
python3 -m unittest discover -s experiments/estimated-ratings/Scripts/tests
```

适配入口将 REPO_ROOT 指向当前代码工作区，因此原脚本的输入默认值和 CLI 参数
继续有效。新输出请显式指定到本目录，不能输出到公开 docs/*.html。
生产 unittest 不递归进入实验目录；站点 HTML 只按公共白名单发布。
