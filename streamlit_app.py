
import os
import requests
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound

# ----------------------
# Helpers
# ----------------------

def iso8601_duration_to_seconds(duration):
    # e.g., PT1H2M10S
    hours = minutes = seconds = 0
    num = ''
    in_time = False
    for ch in duration:
        if ch.isdigit():
            num += ch
        elif ch == 'P':
            continue
        elif ch == 'T':
            in_time = True
        elif ch == 'H' and in_time:
            hours = int(num) if num else 0
            num = ''
        elif ch == 'M' and in_time:
            minutes = int(num) if num else 0
            num = ''
        elif ch == 'S' and in_time:
            seconds = int(num) if num else 0
            num = ''
    return hours*3600 + minutes*60 + seconds

def seconds_to_mmss(total_seconds: int):
    m, s = divmod(int(total_seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def human_int(n):
    try:
        n = int(n)
    except Exception:
        return n
    for unit in ['','천','만','억']:
        if abs(n) < 10000 or unit == '억':
            return f"{n}{unit}"
        n = n//10000

def get_env_api_key():
    key = os.getenv("YOUTUBE_API_KEY", "").strip()
    return key

# ----------------------
# Streamlit UI
# ----------------------
st.set_page_config(page_title="YouTube 탐색 & 대량 비교 + 자막 수집", page_icon="📊", layout="wide")

st.title("📊 YouTube 탐색 & 대량 비교 (2~100개) + 💬 자막 수집")

with st.sidebar:
    st.markdown("### 🔑 API 키 설정")
    st.markdown("Google Cloud 콘솔에서 **YouTube Data API v3** API 키를 발급받아 아래에 입력하세요.")
    api_key_input = st.text_input("YOUTUBE_API_KEY", value=os.getenv("YOUTUBE_API_KEY",""), type="password")
    if api_key_input:
        os.environ["YOUTUBE_API_KEY"] = api_key_input

tab_search, tab_selected = st.tabs(["🔎 탐색/필터 검색", "✅ 선택 영상 비교 & 자막 수집"])

with tab_search:
    col1, col2, col3 = st.columns(3)
    with col1:
        keyword = st.text_input("키워드(비워두면 인기영상)", value="쿠팡꿀템")
        max_results = st.number_input("최대 결과 수", min_value=2, max_value=100, value=10, step=1)
    with col2:
        upload_period = st.selectbox("업로드 시기", options=["전체기간", "이번달", "이번주", "오늘"], index=1)
        duration_filter = st.selectbox("영상 길이", options=["전체", "4분 미만", "4~20분", "20분 이상"], index=1)
    with col3:
        order = st.selectbox("정렬", options=[("viewCount(조회순)","viewCount"),("date(최신순)","date"),("relevance(관련순)","relevance")], format_func=lambda x:x[0])
        select_cap = st.number_input("한 번에 선택할 개수", min_value=2, max_value=50, value=5, step=1)

    if st.button("🔎 검색 실행", type="primary"):
        api_key = get_env_api_key()
        if not api_key:
            st.error("API 키가 필요합니다. 사이드바에 YOUTUBE_API_KEY를 입력해주세요.")
            st.stop()

        # publishedAfter
        now = datetime.now(timezone.utc)
        if upload_period == "이번달":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        elif upload_period == "이번주":
            start = now - timedelta(days=now.weekday())  # Monday
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        elif upload_period == "오늘":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            start = None

        params = {
            "part": "snippet",
            "type": "video",
            "maxResults": min(max_results, 50),  # YouTube API limit per page
            "order": order[1],
            "q": keyword if keyword else None,
            "key": api_key
        }
        if start:
            params["publishedAfter"] = start.isoformat()
        # duration with videoDefinition or videoDuration only works in search if using 'videoDuration' param
        # But search supports videoDuration filter: any, short(<4m), medium(4-20m), long(>20m)
        duration_map = {"전체":"any", "4분 미만":"short", "4~20분":"medium", "20분 이상":"long"}
        params["videoDuration"] = duration_map[duration_filter]

        search_url = "https://www.googleapis.com/youtube/v3/search"
        items = []
        next_page = None
        needed = int(max_results)
        while needed > 0:
            page_params = params.copy()
            if next_page:
                page_params["pageToken"] = next_page
            r = requests.get(search_url, params=page_params, timeout=20)
            r.raise_for_status()
            data = r.json()
            items.extend(data.get("items", []))
            next_page = data.get("nextPageToken")
            if not next_page or len(items) >= max_results:
                break
            needed = max_results - len(items)

        video_ids = [it["id"]["videoId"] for it in items if it.get("id",{}).get("videoId")]
        if not video_ids:
            st.warning("검색 결과가 없습니다.")
            st.stop()

        # Get details
        vid_chunks = [video_ids[i:i+50] for i in range(0, len(video_ids), 50)]
        details = []
        for chunk in vid_chunks:
            params_v = {
                "part": "snippet,contentDetails,statistics",
                "id": ",".join(chunk),
                "key": api_key
            }
            r = requests.get("https://www.googleapis.com/youtube/v3/videos", params=params_v, timeout=20)
            r.raise_for_status()
            details.extend(r.json().get("items", []))

        rows = []
        for it in details:
            vid = it["id"]
            sn = it.get("snippet", {})
            cd = it.get("contentDetails", {})
            stt = it.get("statistics", {})
            dur_sec = iso8601_duration_to_seconds(cd.get("duration", "PT0S"))
            rows.append({
                "선택": False,
                "videoId": vid,
                "썸네일": sn.get("thumbnails",{}).get("default",{}).get("url",""),
                "제목": sn.get("title",""),
                "채널명": sn.get("channelTitle",""),
                "게시일": sn.get("publishedAt","")[:10],
                "조회수": int(stt.get("viewCount","0")) if "viewCount" in stt else 0,
                "댓글수": int(stt.get("commentCount","0")) if "commentCount" in stt else 0,
                "영상 길이(분:초)": seconds_to_mmss(dur_sec),
                "URL": f"https://www.youtube.com/watch?v={vid}",
            })

        df = pd.DataFrame(rows).sort_values(by="조회수", ascending=False if order[1]=="viewCount" else True)
        st.session_state["search_df"] = df
        st.session_state["select_cap"] = int(select_cap)
        st.success(f"가져온 영상 {len(df)}개")
        st.dataframe(df.drop(columns=["videoId"]), use_container_width=True, hide_index=True)

with tab_selected:
    df = st.session_state.get("search_df")
    cap = st.session_state.get("select_cap", 5)
    if df is None:
        st.info("먼저 '탐색/필터 검색' 탭에서 검색을 실행하세요.")
    else:
        st.markdown(f"**한 번에 선택할 개수 제한:** {cap}개")
        editable_df = st.data_editor(
            df,
            column_config={
                "썸네일": st.column_config.ImageColumn("썸네일", help="영상 썸네일"),
                "선택": st.column_config.CheckboxColumn("선택", help="자막 수집 대상 선택"),
                "URL": st.column_config.LinkColumn("URL"),
            },
            hide_index=True,
            use_container_width=True,
            num_rows="dynamic",
        )

        selected = editable_df[editable_df["선택"] == True]
        if len(selected) > cap:
            st.error(f"선택 개수 {len(selected)}개 — 제한 {cap}개 이하로 줄여주세요.")
            st.stop()

        if st.button("💬 선택 영상 자막 수집"):
            transcripts = []
            for _, row in selected.iterrows():
                vid = row["videoId"]
                title = row["제목"]
                url = row["URL"]
                # Try KO first then auto
                langs_try = ["ko", "a.en", "en"]
                transcript_text = ""
                found_lang = None
                try:
                    available = YouTubeTranscriptApi.list_transcripts(vid)
                    # prefer Korean, else first translatable
                    for pref in ["ko", "ja", "en"]:
                        if available.find_manually_created_transcript([pref]):
                            t = available.find_manually_created_transcript([pref]).fetch()
                            transcript_text = " ".join([x["text"] for x in t])
                            found_lang = pref
                            break
                    if not transcript_text:
                        # fallback: any
                        t = available.find_generated_transcript(available._TranscriptList__languages).fetch()
                        transcript_text = " ".join([x["text"] for x in t])
                        found_lang = t[0].get("language", "auto")
                except (TranscriptsDisabled, NoTranscriptFound):
                    transcript_text = "(자막 없음)"
                except Exception as e:
                    transcript_text = f"(에러: {e})"

                transcripts.append({
                    "제목": title,
                    "URL": url,
                    "자막": transcript_text
                })

            tdf = pd.DataFrame(transcripts)
            st.session_state["transcripts_df"] = tdf
            st.success(f"자막 수집 완료: {len(tdf)}개")
            st.dataframe(tdf[["제목","URL"]], use_container_width=True, hide_index=True)

        tdf = st.session_state.get("transcripts_df")
        if tdf is not None and not tdf.empty:
            colA, colB = st.columns(2)
            with colA:
                csv = tdf.to_csv(index=False).encode("utf-8-sig")
                st.download_button("⬇️ 자막 CSV 다운로드", data=csv, file_name="youtube_transcripts.csv", mime="text/csv")
            with colB:
                # plain text export
                lines = []
                for _, r in tdf.iterrows():
                    lines.append(f"# {r['제목']}\n{r['URL']}\n{r['자막']}\n")
                txt = "\n\n".join(lines).encode("utf-8")
                st.download_button("⬇️ 자막 TXT 다운로드", data=txt, file_name="youtube_transcripts.txt", mime="text/plain")
