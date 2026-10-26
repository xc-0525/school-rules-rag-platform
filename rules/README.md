# 校规文档目录

把学校的真实校规文档放在这里，然后执行：

```bash
cd backend
python scripts/ingest_rules.py --dir ../rules --reset
```

支持的格式：`.md`、`.txt`（首版）。后续会加 `.pdf` / `.docx` 解析。

当前仓库里的 `学生手册节选.md` 是**开发演示用的虚构样例**，用于跑通
"文档 → 解析切分 → MySQL 记录 → BGE 向量 → Qdrant → Neo4j 图谱"全链路；
拿到真实文档后直接替换/追加即可，解析器和入库脚本不需要改。
