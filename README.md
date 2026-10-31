# 미국 증시 한눈에 (US Stock Wire)

## 로컬 실행
```
pip install -r requirements.txt
python app.py        # http://localhost:5000  (아이폰은 같은 Wi-Fi에서 PC의 IP:5000)
```

## 웹서비스 배포 (Render, 무료 플랜 가능)
1. 이 폴더를 GitHub 저장소에 올립니다 (`git init → git add . → git commit → git push`).
2. render.com → **New → Blueprint** → 저장소 선택 (`render.yaml`을 자동 인식).
   또는 **New → Web Service**에서 Start Command에 `gunicorn app:app --workers 1 --threads 8 --timeout 60 --bind 0.0.0.0:$PORT` 입력.
3. 배포가 끝나면 `https://<이름>.onrender.com` 로 접속됩니다. 상태 확인: `/healthz`, 데이터: `/api/news`.
4. 도메인 연결은 Render 대시보드 → Settings → Custom Domains.

`Procfile`이 있어 Railway·Heroku류에서도 그대로 동작합니다.

## 환경변수
- `CACHE_SECONDS` (기본 300): 뉴스 갱신 주기(초)
- `FLASK_DEBUG=1`: 로컬 개발에서만 사용 (운영 금지)

## 운영 시 알아둘 점
- 무료 플랜은 일정 시간 접속이 없으면 잠들고, 첫 접속은 수십 초 걸릴 수 있습니다.
- 워커를 1개로 둔 이유: 메모리 캐시를 공유해 RSS를 중복 호출하지 않기 위해서입니다. 트래픽이 커지면 Redis 캐시로 바꾸세요.
- RSS 주소가 막히면 `/api/news`의 `failed`에 표시되고, 5건 미만이면 샘플 데이터로 전환됩니다.
- 헤드라인 공개 서비스는 각 언론사 이용약관을 확인하세요. 현재는 제목·요약 일부와 원문 링크만 노출합니다.
