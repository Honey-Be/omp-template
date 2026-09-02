# NVIDIA CUDA 13.2.1 환경이 구성된 SUSE Enterprise 16 (devel) 베이스 이미지 사용
FROM docker.io/nvidia/cuda:13.2.1-devel-suse16

# 1. uv 공식 바이너리를 컨테이너로 직접 복사
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 2. C 확장을 위한 필수 시스템 패키지 설치 (Python 설치는 uv에 위임)
RUN zypper refresh && zypper install -y \
    gcc \
    gcc-c++ \
    make \
    sqlite3-devel \
    tar \
    gzip \
    && zypper clean -a

WORKDIR /app

# 3. 의존성 파일 복사 및 uv를 활용한 동기화
# uv가 스스로 적절한 Python 3.14 런타임을 다운로드하고 패키지를 설치합니다.
# uv.lock 을 함께 복사하고 --locked 로 동기화합니다. --locked 는 락파일이
# pyproject.toml 과 어긋날 때 조용히 재해석하는 대신 빌드를 실패시키므로,
# 이미지가 언제 빌드되어도 같은 버전을 담는다는 보장이 여기서 생깁니다.
# (락파일 없이 uv sync 하던 이전 방식은 빌드 시점마다 해석 결과가 달랐습니다.)
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --python 3.14

# 4. 서버 스크립트 복사
COPY daemon.py .

# 5. SQLite 데이터베이스가 저장될 볼륨 디렉토리 생성
RUN mkdir -p /app/data

# 6. 진단 로그를 버퍼링 없이 즉시 흘려보냅니다.
# daemon.py 는 print() 로 상태와 오류를 남깁니다. 컨테이너의 stdout 은 파이프
# 이므로 기본이 블록 버퍼링이고, uvicorn 로그는 stderr 로 즉시 나갑니다. 그래서
# startup 에서 찍는 스키마 검증 메시지가 "Application startup complete" 보다
# 늦게 나타나 초기화가 누락된 것처럼 보였습니다. 컨테이너가 급히 종료되면
# 버퍼에 남은 진단(예: Search FTS 경로 실패)이 유실될 수도 있습니다.
ENV PYTHONUNBUFFERED=1

# 7. FastAPI 포트 노출
EXPOSE 8016

# 8. uv run을 사용하여 고립된 3.14 환경에서 Uvicorn 실행
CMD ["uv", "run", "--python", "3.14", "uvicorn", "daemon:app", "--host", "0.0.0.0", "--port", "8016"]
