# justfile for oh-my-pi workspace management
default:
    @just --list

# 이 워크스페이스의 이름. `.omp/workspace.yml` 의 `name` 이 단일 출처이므로 프로젝트마다
# justfile 을 고칠 필요가 없습니다 — 템플릿에서 그대로 재사용 가능합니다.
#
# `name` 을 못 읽으면 디렉터리 이름으로 폴백합니다. 빈 문자열로 두면 컨테이너 이름이
# `-memory-daemon` 같은 형태가 되어 조용히 잘못된 것이 만들어지므로, 폴백이 필요합니다.
# IPC 수신 시 이름이 어긋나면 발신함 경로가 존재하지 않아 `_receive` 가 경로를 찍으며
# 실패하므로, 잘못된 이름은 그 지점에서 드러납니다.
SELF := `n=$(sed -n 's/^name:[[:space:]]*"\?\([^"]*\)"\?[[:space:]]*$/\1/p' .omp/workspace.yml 2>/dev/null | head -1); [ -n "$n" ] && printf '%s' "$n" || basename "$PWD"`

# 1. 통합 워크스페이스 구조 및 IPC 통신 디렉터리 초기화
init-workspace:
    mkdir -p .omp/data
    mkdir -p .local-requests-from
    mkdir -p .local-requests-to
    mkdir -p .local-replies-from
    mkdir -p .local-replies-to
    mkdir -p .local-mentions-by
    # 루트 디렉터리의 Python 3.14 환경 의존성 동기화
    uv sync --python 3.14
    podman build --no-cache -t {{SELF}}-omp-memory-daemon -f .omp/server/Dockerfile .omp/server/
    @echo "데이터베이스 준비, IPC 디렉터리 세팅 및 uv 환경 구축이 완료되었습니다."

#   .omp/server/Dockerfile 은 uv sync --locked 로 이미지를 만듭니다. 락파일이
#   pyproject.toml 과 어긋나면 빌드가 실패하므로, 데몬 의존성을 고친 뒤에는
#   반드시 이 레시피로 락파일을 갱신하고 다시 빌드하십시오.
#   (uv lock 은 해석만 하므로 .venv 를 만들지 않습니다. 컨테이너 전용
#    의존성을 호스트에 설치하지 않으려면 이 디렉터리에서 uv run/uv sync 는
#    쓰지 마십시오.)

# 1b. 데몬 의존성 락파일 갱신 (.omp/server/pyproject.toml 변경 후 실행)
lock-daemon:
    cd .omp/server && uv lock --python 3.14

#   --network host 를 쓰므로 -p 포트 매핑은 지정하지 않습니다. podman 이
#   "Port mappings have been discarded because host network namespace mode does
#   not support them" 경고와 함께 버리던 설정이었습니다. 컨테이너가 호스트
#   네트워크 이름공간을 그대로 쓰므로 uvicorn 의 0.0.0.0:8016 바인딩이 곧
#   호스트의 8016 이며, 매핑은 의미가 없습니다.

# 2. 데몬 컨테이너 실행 (NVIDIA GPU 리소스 패스스루 포함)
run-daemon:
    podman run -d --name {{SELF}}-memory-daemon \
        --network host \
        --device nvidia.com/gpu=all \
        -v $(pwd)/.omp/data:/app/data \
        {{SELF}}-omp-memory-daemon

# 3. 데몬 컨테이너 중지 및 삭제 (오류 방지를 위해 실패 시 무시)
stop-daemon:
    podman stop {{SELF}}-memory-daemon || true
    podman rm {{SELF}}-memory-daemon || true

# 4. 기본 모델로 에이전트 시작
start OMP_CONFIG_FILE=".omp/workspace.yml":
    omp start --config "{{OMP_CONFIG_FILE}}"

# 5. 민감 정보 스캐너 구동 테스트
scan-privacy TEXT:
    uv run --python 3.14 .omp/scripts/privacy_filter.py "{{TEXT}}"

#   민감한 원문을 argv 로 넘기면 프로세스 목록과 셸 히스토리에 평문이 남습니다.
#   SYSTEM.md 지침 4(Zero-Leakage) 를 지키려면 위 scan-privacy 대신 이 레시피를
#   쓰십시오. 본문은 표준입력으로만 흐르므로 인용 처리도 필요하지 않습니다.
#   사용 예:
#     just scan-privacy-stdin <<'EOF'
#     검사할 본문, "따옴표", 여러 줄 모두 안전
#     EOF

# 5a. 민감 정보 스캐너 (본문을 stdin 으로 — 노출/인용 안전)
scan-privacy-stdin:
    uv run --python 3.14 .omp/scripts/privacy_filter.py -

#   workspace.yml 의 security.privacyScanner.patterns 와 privacy_filter.py 의
#   SENSITIVE_PATTERNS 가 순서까지 동일한지 확인합니다. 두 목록은 소비자가
#   달라(런타임 vs 스크립트) 한 곳으로 합칠 수 없으므로 검사로 묶어 둡니다.

# 5b. 민감 정보 패턴 정의 일치 검사
verify-patterns:
    uv run --python 3.14 .omp/scripts/verify_patterns.py

# 6. 특정 폴백 모델로 수동 전환하여 시작
switch-model MODEL:
    omp start --config .omp/workspace.yml --model "{{MODEL}}"

# 7. 현재 세션 데이터를 메모리 DB 데몬에 저장
save-memory SESSION_ID ROLE CONTENT TAGS:
    uv run --python 3.14 .omp/scripts/memory_client.py save "{{SESSION_ID}}" "{{ROLE}}" "{{CONTENT}}" "{{TAGS}}"

# 본문을 표준입력으로 넘깁니다. CONTENT 인터폴레이션은 값 내부의
# 따옴표나 매우 긴 본문을 안전하게 전달하지 못하므로, 설계 노트처럼 긴
# 텍스트는 반드시 이 레시피를 쓰십시오. 사용 예:
#   just save-memory-stdin my-session system 'tag1,tag2' <<'EOF'
#   여러 줄, "따옴표", 특수문자가 포함된 본문
#   EOF
# 7b. 메모리 저장 (본문을 stdin 으로 — 인용 안전)
save-memory-stdin SESSION_ID ROLE TAGS:
    uv run --python 3.14 .omp/scripts/memory_client.py save "{{SESSION_ID}}" "{{ROLE}}" - "{{TAGS}}"

# 8. 메모리 DB 데몬 검색 테스트
search-memory QUERY LIMIT="5":
    uv run --python 3.14 .omp/scripts/memory_client.py search "{{QUERY}}" "{{LIMIT}}"

# 9. 컨텍스트 데이터베이스 JSON 파일로 추출 (Logical Backup)
export-db FILEPATH="context_backup.json":
    uv run --python 3.14 .omp/scripts/memory_client.py export "{{FILEPATH}}"

# 10. JSON 파일로부터 컨텍스트 데이터베이스 적재 (Vector Re-embedding)
import-db FILEPATH="context_backup.json":
    uv run --python 3.14 .omp/scripts/memory_client.py import "{{FILEPATH}}"

# 11. CUA(컴퓨터 사용) 어댑터 독립 테스트 구동
test-cua:
    uv run --python 3.14 .omp/scripts/cua_adapter.py test

# 12. 다른 프로젝트로 메시지 발송 (IPC 파일 생성)
send-message RECEIVER MESSAGE:
    mkdir -p ".local-requests-to/{{RECEIVER}}"
    echo "{{MESSAGE}}" > ".local-requests-to/{{RECEIVER}}/$(date +%s).md"
    @echo "{{RECEIVER}} 프로젝트로 메시지 발송이 예약되었습니다."

#   타 프로젝트가 우리에게 보낸 것을 받아옵니다. 방향이 둘입니다.
#
#     just receive-replies-from  <프로젝트> [발신함 경로]   # 회신
#     just receive-requests-from <프로젝트> [발신함 경로]   # 요청
#
#   기본 발신함 경로는 형제 디렉터리 배치를 가정합니다:
#     ../<프로젝트>/.local-{replies,requests}-to/<우리 이름>
#   우리 이름은 `.omp/workspace.yml` 의 `name` 에서 도출합니다(위 SELF). 배치가 다르면 두
#   번째 인자로 경로를 직접 넘기십시오.
#
#   멱등입니다. rsync -a 가 시각을 보존하므로 이미 받은 파일은 재전송되지 않고 새로 온 것만
#   출력합니다. 반복 실행해도 안전합니다.

# 12b. 타 프로젝트의 회신 수신 (IPC 수신)
receive-replies-from PROJECT SRC=("../" + PROJECT + "/.local-replies-to/" + SELF):
    @just _receive "{{PROJECT}}" "{{SRC}}" replies

# 12c. 타 프로젝트의 요청 수신 (IPC 수신)
receive-requests-from PROJECT SRC=("../" + PROJECT + "/.local-requests-to/" + SELF):
    @just _receive "{{PROJECT}}" "{{SRC}}" requests

# 두 수신 방향의 공통 본문. KIND 는 `replies` 또는 `requests`.
[private]
_receive PROJECT SRC KIND:
    #!/usr/bin/env sh
    set -eu
    case "{{KIND}}" in
        replies)  label="회신" ;;
        requests) label="요청" ;;
        *) echo "알 수 없는 종류: {{KIND}} (replies 또는 requests)" >&2 ; exit 1 ;;
    esac
    # SELF 는 폴백이 있어 비지 않습니다(위 정의 참조). 이름이 어긋나면 아래 경로 검사가
    # 전체 경로를 찍으며 실패하므로 그 지점에서 드러납니다.
    src="{{SRC}}"
    dst=".local-{{KIND}}-from/{{PROJECT}}"
    if [ ! -d "$src" ]; then
        echo "발신함이 없습니다: $src" >&2
        echo "배치가 형제 구조가 아니면 경로를 직접 넘기십시오:" >&2
        echo "  just receive-{{KIND}}-from {{PROJECT}} /path/to/.local-{{KIND}}-to/{{SELF}}" >&2
        exit 1
    fi
    mkdir -p "$dst"
    # 원본에 트레일링 슬래시를 씁니다. `$src/*` 는 셸 글로브라 발신함이 비어 있으면 확장에
    # 실패해 rsync 가 오류를 내고, 숨김 파일도 놓칩니다. `$src/` 는 두 경우 모두 안전하며
    # "내용을 대상 안으로 복사"라는 의미는 동일합니다.
    #
    # --delete 는 의도적으로 쓰지 않습니다. 발신 측이 자기 발신함을 정리해도 우리가 받은
    # 기록은 남아야 합니다 — .gitignore 주석대로 프로젝트 간 왕래 기록이 곧 설계 근거입니다.
    received=$(rsync -a --out-format='%n' "$src/" "$dst/" | grep -v '/$' || true)
    if [ -z "$received" ]; then
        echo "새 ${label} 없음 ({{PROJECT}})"
    else
        printf '%s\n' "$received" | sed 's/^/  받음: /'
        printf '%s 에서 %s건 수신 -> %s\n' "{{PROJECT}}" "$(printf '%s\n' "$received" | wc -l | tr -d ' ')" "$dst"
    fi

# 13. 임시 파일 정리
clean:
    rm -rf .omp/tmp/*

# 주의: TARGETS 는 따옴표가 보존되지 않고 공백으로 분할되므로, 공백이 포함된
# SESSION_ID 는 이 레시피로 넘길 수 없습니다. 그 경우 아래를 직접 쓰십시오:
#   uv run --python 3.14 .omp/scripts/memory_client.py delete "session:<...>"
# 14. 메모리 레코드 삭제 (id 나열, 또는 session:<SESSION_ID>)
delete-memory *TARGETS:
    uv run --python 3.14 .omp/scripts/memory_client.py delete {{TARGETS}}

# --- git via dev env ------------------------------------------------------

# Run the git CLI with `.env.dev-git` applied, then forward all args verbatim.
# `.env.dev-git` (gitignored) points SSH_ASKPASS at ksshaskpass so SSH
# pushes/fetches prompt via the KDE dialog instead of failing on the absent
# /usr/lib/ssh/ssh-askpass.  If the env file is missing, git still runs.
#
# Usage:  just git <git-args...>            e.g.  just git push origin main
#
# NOTE: do NOT insert `--`. `just` passes it through to git verbatim, which
# then fails with "unknown option: --". Verified:
#     just git -- status --short   ->  git -- status --short   ->  error
#     just git status --short      ->  works
# Trailing flags are fine because git takes a subcommand first, so `just`
# never sees a leading `-` for this recipe.
#
# NOTE 2: quoting is NOT preserved. `{{ARGS}}` expands unquoted, so any
# multi-word argument is split into separate words. Verified:
#     just git commit -m "two words"
#         -> git commit -m two words   -> "pathspec 'words' did not match"
# Pass anything containing spaces on stdin instead. For commit messages:
#     just git commit -q -F - <<'EOF'
#     subject line
#
#     body
#     EOF
# This is why every commit in this repo uses `-F -` rather than `-m`.
git *ARGS:
    SSH_ASKPASS=/usr/bin/ksshaskpass SSH_ASKPASS_REQUIRE=force git {{ARGS}}

# Mirror a recipient's reply outbox to that recipient's expected
# inbox path. Example:
#
#   just mirror-local-requests-to ypeg ~/ypeg/.local-requests-from/<우리 이름>/
#
# The recipe is a no-op (with a friendly message) when
# `.local-requests-to/<recipient>/` is empty or missing.
mirror-local-requests-to recipient target:
    @src=".local-requests-to/{{recipient}}" ; \
        if [ -d "$src" ] && [ -n "$(ls -A "$src" 2>/dev/null)" ]; then \
            mkdir -p '{{target}}' ; \
            rsync -a "$src/" '{{target}}' ; \
            echo "✓ Mirrored $src/ → {{target}}" ; \
        else \
            echo "⚠ $src/ empty or missing — nothing to mirror to {{target}}" ; \
        fi

# Mirror a recipient's reply outbox to that recipient's expected
# inbox path. Example:
#
#   just mirror-local-replies-to ypeg ~/ypeg/.local-replies-from/<우리 이름>/
#
# The recipe is a no-op (with a friendly message) when
# `.local-replies-to/<recipient>/` is empty or missing.
mirror-local-replies-to recipient target:
    @src=".local-replies-to/{{recipient}}" ; \
        if [ -d "$src" ] && [ -n "$(ls -A "$src" 2>/dev/null)" ]; then \
            mkdir -p '{{target}}' ; \
            rsync -a "$src/" '{{target}}' ; \
            echo "✓ Mirrored $src/ → {{target}}" ; \
        else \
            echo "⚠ $src/ empty or missing — nothing to mirror to {{target}}" ; \
        fi
