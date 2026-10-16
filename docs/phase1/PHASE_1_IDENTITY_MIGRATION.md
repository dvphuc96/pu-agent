# Phase 1 — Identity Migration Report

**Ngày:** 2026-10-04  
**Project identity:** `pu-agent`  
**Python package:** `pu`  
**CLI:** `pu`  
**Runtime home:** `.pu/`  
**Environment namespace:** `PU_*`

## 1. Mục tiêu

Phase 1 chỉ thực hiện **identity migration có kiểm soát**, không refactor agent loop, Tool Registry, provider adapter, persistence hay external integrations.

Mục tiêu là đưa toàn bộ codebase baseline sang identity PU Agent trước khi bắt đầu prune/refactor kiến trúc ở các phase sau.

## 2. Assumption đã dùng

Project đang ở giai đoạn migration nội bộ, chưa có deployment production cần backward compatibility với namespace/CLI/runtime-home cũ. Vì yêu cầu là đổi toàn bộ identity, Phase 1 dùng **atomic migration** thay vì giữ compatibility alias.

Nếu sau này cần import state từ một installation cũ, việc đó phải được làm bằng migration command/script riêng; không giữ hai namespace song song trong runtime mới.

## 3. Thay đổi đã thực hiện

### Project/package/CLI

- distribution name: `pu-agent`;
- Python package root: `pu/`;
- module invocation: `python -m pu`;
- installed CLI entry point: `pu = "pu.__main__:main"`;
- wheel package target: `pu`;
- bundled skills target: `pu/skills`;
- application class chính: `PU`.

### Config/runtime identity

- toàn bộ environment namespace chuyển sang `PU_*`;
- default runtime home là `.pu`;
- token/state/model/workspace/traces path trong code/docs/static UI dùng `.pu/...`;
- examples, evals, scripts và tool modules dùng import path `pu.*`.

### UI/docs/assets

- dashboard title/branding dùng `PU Agent`;
- static JS/help text dùng PU naming;
- architecture whiteboard source đổi sang `pu-architecture.excalidraw`;
- architecture PNG/JPG được regenerate từ source mới để không giữ branding/path cũ;
- screenshot reference cũ được bỏ khỏi source Phase 1 vì chứa branding/runtime path cũ;
- các URL repository bị tạo ra do rename cơ học đã được gỡ thay vì đoán canonical remote URL chưa được cung cấp.

### Provenance

- thêm `UPSTREAM_NOTICE.md` để giữ provenance của source bundle `Archive.zip` và attribution trong package metadata;
- không khai báo remote project URL giả trong `pyproject.toml`.

### Tests

- thêm `evals/deterministic/test_pu_identity.py` với ba invariant:
  1. distribution/package/CLI đều là PU;
  2. runtime home mặc định là `.pu` và `PU_HOME` override hoạt động;
  3. repository không còn legacy identity trong path hoặc UTF-8 text source.

## 4. Mechanical migration audit

So với snapshot Phase 0, sau khi áp dụng đúng phép đổi identity cơ học:

- **263 files** khớp byte-for-byte với kết quả mechanical transform dự kiến;
- chỉ có **9 files** có intentional delta ngoài mechanical transform;
- **1 stale UI screenshot** được bỏ vì còn branding/runtime path cũ.

Các intentional delta gồm:

- `pyproject.toml`: bỏ remote URLs chưa được xác minh;
- `ARCHITECTURE.md`: cập nhật migration sequence, đặt Identity Migration thành Phase 1;
- `README.md`: bỏ URL/social identity bị rename thành địa chỉ không có căn cứ và dùng repository placeholder;
- `docs/architecture.html`: bỏ remote URL chưa được xác minh;
- `pu/runtime/session.py`: bỏ source URL chưa được xác minh khỏi system context;
- whiteboard metadata/source: dùng PU identity và regenerate visual asset.

Điều này giúp giới hạn Phase 1 thành rename/branding/config identity, không lẫn với OOP refactor hay behavior change.

## 5. Validation gate

| Gate | Kết quả |
|---|---|
| Legacy identity trong path | **0** |
| Legacy identity trong byte/text scan của repo | **0** |
| Python `compileall` | **PASS** |
| Static JavaScript syntax (`node --check`) | **PASS** |
| `pyproject.toml` identity assertions | **PASS** |
| `Settings().home == .pu` | **PASS** |
| `test_pu_identity.py` | **3 passed** |
| Phase-0 targeted characterization set | **55 passed, 1 skipped, 5 dependency-blocked** |
| CLI module smoke (`python -m pu <unknown>`) | **PASS**: module/entrypoint resolve và help text dùng PU identity |

Targeted characterization set cho kết quả **giống Phase 0**: 5 failures vẫn là `ModuleNotFoundError: anthropic` do sandbox không cài core SDK. Không tạo stub/fake SDK để làm xanh test.

## 6. Full-suite blockers vẫn còn từ môi trường/baseline

`pytest --collect-only evals/deterministic` hiện collect được 224 tests trước khi dừng với 41 import errors do thiếu package `anthropic`.

Một nhóm test bảo vệ runtime secrets/git-ignore cũng không thể đánh giá đúng trong source bundle hiện tại vì `Archive.zip` không chứa Git metadata, `.gitignore` hoặc `.env.example`. Đây là limitation của source bundle/baseline, không phát sinh từ identity migration.

Package build thực tế chưa được chạy trong sandbox vì build backend `hatchling` không được cài và môi trường không có outbound package network. `pyproject.toml` được parse/assert trực tiếp để kiểm tra package/CLI/wheel identity.

## 7. Exit criteria Phase 1

- [x] Project name là `pu-agent`.
- [x] Python package là `pu`.
- [x] CLI là `pu`.
- [x] Runtime state namespace là `.pu`.
- [x] Environment namespace là `PU_*`.
- [x] Imports/tests/examples/scripts/static UI/docs đã migrate.
- [x] Không còn legacy identity trong source/path scan.
- [x] Architecture whiteboard đã regenerate bằng PU identity.
- [x] Python/JS syntax gate pass.
- [x] Characterization baseline không có regression mới ngoài dependency blockers đã biết.
- [x] `ARCHITECTURE.md` đã đồng bộ migration sequence.

**Kết luận:** Phase 1 hoàn tất. Chưa thực hiện prune capability, OOP refactor, provider-neutral rewrite, Tool Harness hay Security/Human Approval implementation.

## 8. Phase tiếp theo

Theo `ARCHITECTURE.md`, **Phase 2** là prune scope nhưng chưa đổi core execution: giữ Anthropic/OpenAI, Gmail/Calendar/Drive/Search/UI và characterization coverage cần thiết; loại dần gateway/provider/tool/benchmark ngoài requirements sau khi dependency mapping xác nhận an toàn.
