# 오늘의 아침 브리핑

매일 아침 날씨, 미국·한국 증시, 외신 헤드라인을 모아 보여주는 페이지입니다.
데이터(`data/*.json`)는 매일 오전 7시쯤 자동으로 갱신됩니다.

## 자동 갱신

GitHub Actions(`.github/workflows/update.yml`)가 매일 한국 시간 오전 7시쯤 `scripts/update.py`를 실행해 데이터를 새로 받고 커밋합니다.
Actions 탭에서 **Run workflow** 버튼을 누르면 바로 한 번 실행할 수도 있습니다.

- 날씨: Open-Meteo / 증시: 네이버 증권, Yahoo Finance / 뉴스: 각 언론사 RSS
- 저장소 **Settings → Secrets and variables → Actions**에 `ANTHROPIC_API_KEY`를 넣으면 Claude가 헤드라인을 골라 우리말로 옮기고 분야별로 나눕니다. 없으면 영어 제목을 언론사별로 보여 줍니다.
