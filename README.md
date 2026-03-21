# autocoin

빗썸 연동 자동매매 서버 (Python).

## 빠른 시작

```bash
cd /path/to/autocoin
./.venv/bin/pip install -e .
./.venv/bin/python -m autocoin
```

Private API(잔고) 확인: `.env.example`을 참고해 `.env`에 키를 넣은 뒤:

```bash
./.venv/bin/python -m autocoin --accounts
```

## 시스템 패키지 (Ubuntu / Debian)

터미널에서 한 번 실행:

```bash
sudo apt-get update
sudo apt-get install -y direnv python3.12-venv pre-commit
```

설치 후 **가상환경을 표준 방식으로 다시 만들려면** (이미 `.venv`가 있으면 교체):

```bash
cd /path/to/autocoin
rm -rf .venv
python3 -m venv .venv
./.venv/bin/pip install -U pip
```

`pre-commit`은 apt로 깔면 시스템 `PATH`의 `pre-commit`을 쓰면 됩니다. 저장소에서 훅만 연결:

```bash
cd /path/to/autocoin
pre-commit install
```

## 셸 설정

- **direnv**: `~/.bashrc`(또는 zsh/fish)에 훅 추가 — `eval "$(direnv hook bash)"`
- 프로젝트 디렉터리 진입 시 `.envrc` 로드, 최초에는 `direnv allow` 필요할 수 있음.

## 구성 요약

- **가상환경**: 루트의 `.venv` — direnv가 `PATH`에 넣음 (`.envrc`).
- **pre-commit**: 커밋 시 `.pre-commit-config.yaml` 훅 실행.

## 참고 (uv)

`python3-venv`를 쓸 수 없을 때만, [uv](https://github.com/astral-sh/uv)로 `.venv`를 만들 수 있습니다.
