# Issue tracker: GitHub

本 Repository 的未完成工作與 PRD 使用 GitHub Issues。

## Tracking scope

只建立自 `2026-07-26` 起仍未完成的：

- 功能
- Bug
- 實機驗證
- 文件整理

不替已完成項目補建歷史 Issue；完成歷史由 Git、tag 與 release 保存。

## Conventions

- 建立：GitHub connector，或 `gh issue create --title "..." --body "..."`
- 讀取：`gh issue view <number> --comments`
- 列出：`gh issue list --state open`
- 留言：`gh issue comment <number> --body "..."`
- 加減 label：`gh issue edit <number> --add-label "..."`／`--remove-label "..."`
- 關閉：`gh issue close <number> --comment "..."`

在 Repository clone 內執行時，由 `git remote` 推斷 Repository。

## Pull requests as a triage surface

**PRs as a request surface: no.**

## Skill operations

- 「publish to the issue tracker」：建立 GitHub Issue。
- 「fetch the relevant ticket」：讀取指定 Issue 與 comments。
