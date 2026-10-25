# 本机 API 路由

由 `tools/build_api_surface.py` 从 `cellflow/api.py` 生成。
默认只监听 127.0.0.1，供桌面窗口使用。

| 方法 | 路径 | 处理函数 |
|---|---|---|
| GET | `/api/datasets` | `datasets` |
| POST | `/api/datasets/demo` | `make_demo` |
| GET | `/api/datasets/inspect` | `inspect_dataset` |
| POST | `/api/datasets/public` | `public_fetch` |
| POST | `/api/datasets/upload` | `upload` |
| DELETE | `/api/datasets/{dataset_id}` | `delete_dataset` |
| GET | `/api/datasets/{dataset_id}/detail` | `dataset_detail` |
| GET | `/api/downloads` | `download_list` |
| POST | `/api/downloads` | `start_download` |
| GET | `/api/downloads/{task_id}` | `download_status` |
| POST | `/api/downloads/{task_id}/cancel` | `download_cancel` |
| GET | `/api/env` | `environment` |
| GET | `/api/health` | `health` |
| GET | `/api/logs/{job_id}` | `run_log` |
| GET | `/api/meta` | `meta` |
| GET | `/api/methods` | `methods_route` |
| POST | `/api/project` | `rename_project` |
| POST | `/api/projects` | `create_project` |
| DELETE | `/api/projects/{project_id}` | `delete_project` |
| POST | `/api/projects/{project_id}/activate` | `activate_project` |
| POST | `/api/projects/{project_id}/archive` | `archive_project` |
| GET | `/api/runs` | `runs` |
| POST | `/api/runs` | `create_run` |
| DELETE | `/api/runs/{job_id}` | `delete_run` |
| GET | `/api/runs/{job_id}` | `run_detail` |
| PATCH | `/api/runs/{job_id}` | `rename_run` |
| POST | `/api/runs/{job_id}/cancel` | `cancel_run` |
| GET | `/api/runs/{job_id}/compare/{other_id}` | `compare_runs` |
| GET | `/api/runs/{job_id}/figure/{name}` | `run_figure` |
| GET | `/api/runs/{job_id}/report/{kind}` | `run_report` |
| GET | `/api/runs/{job_id}/report/{kind}/pdf` | `run_report_pdf` |
| GET | `/api/runs/{job_id}/zip` | `run_zip` |
| GET | `/api/terms` | `terms` |
| GET | `/openapi.json` | `openapi` |

## 维护提示

- `/api/datasets/{id}/detail` 必须绑定到 `dataset_detail`，不能绑定内部辅助函数；
  `tests/test_api_surface.py` 会检查处理函数名不以下划线开头。
- 新增路由后重新运行本脚本，保持文档与代码一致。
