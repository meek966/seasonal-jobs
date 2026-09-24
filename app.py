
import io, json, re, zipfile
from datetime import date, timedelta
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="US Seasonal Jobs Finder",
    page_icon="🇺🇸",
    layout="wide",
)

FEED_BASE = "https://api.seasonaljobs.dol.gov/datahub-search/sjCaseData/zip"
SITE_BASE = "https://seasonaljobs.dol.gov"
TIMEOUT = 45

@st.cache_data(ttl=6*60*60, show_spinner=False)
def download_feed(feed_kind: str, feed_date: str):
    url = f"{FEED_BASE}/{feed_kind}/{feed_date}"
    r = requests.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.content, url

def previous_working_feed_date():
    # The DOL feed is daily; try today and the preceding few dates.
    d = date.today()
    return [d - timedelta(days=i) for i in range(0, 8)]

def load_latest_feed(kind="jo"):
    errors = []
    for d in previous_working_feed_date():
        try:
            content, url = download_feed(kind, d.isoformat())
            return content, url, d.isoformat()
        except Exception as e:
            errors.append(f"{d.isoformat()}: {e}")
    raise RuntimeError("Could not download a recent DOL feed.\n" + "\n".join(errors))

def find_json_objects(obj):
    if isinstance(obj, list):
        for x in obj:
            yield x
    elif isinstance(obj, dict):
        # Some feeds may wrap records in a top-level key.
        for key in ("data", "results", "jobs", "records", "items", "jobOrders"):
            if key in obj and isinstance(obj[key], list):
                for x in obj[key]:
                    yield x
                return
        yield obj

def parse_zip_json(content: bytes):
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        json_names = [n for n in names if n.lower().endswith(".json")]
        if not json_names:
            raise ValueError(f"No JSON file found in feed archive. Files: {names[:10]}")
        # Prefer the largest JSON file; feeds sometimes include metadata files.
        name = max(json_names, key=lambda n: z.getinfo(n).file_size)
        raw = z.read(name).decode("utf-8-sig")
    data = json.loads(raw)
    records = list(find_json_objects(data))
    return records, name

def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())

def flatten(obj, prefix=""):
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, (dict, list)):
                out.update(flatten(v, p))
            else:
                out[norm(p)] = v
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(flatten(v, f"{prefix}.{i}"))
    return out

def first_value(flat, aliases):
    aliases = [norm(a) for a in aliases]
    # exact key match first
    for a in aliases:
        for k, v in flat.items():
            if k == a and v not in (None, ""):
                return v
    # then suffix match
    for a in aliases:
        for k, v in flat.items():
            if k.endswith(a) and v not in (None, ""):
                return v
    return ""

def normalize_case_number(v):
    s = str(v or "").strip()
    # Keep the familiar H-2A/H-2B case format if present.
    m = re.search(r"H-[0-9]+-[0-9A-Za-z-]+", s, re.I)
    return m.group(0) if m else s

def clean_phone(v):
    s = str(v or "").strip()
    return re.sub(r"[^0-9+(). extx-]", "", s)

def clean_email(v):
    s = str(v or "").strip()
    m = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", s, re.I)
    return m.group(0) if m else ""

def clean_url(v):
    s = str(v or "").strip()
    if s and not s.lower().startswith(("http://", "https://")):
        return "https://" + s
    return s

def record_to_row(rec):
    f = flatten(rec)

    case_no = normalize_case_number(first_value(f, [
        "caseNumber", "caseNo", "caseID", "caseId", "jobOrderCaseNumber",
        "h2aCaseNumber", "h2bCaseNumber"
    ]))

    title = first_value(f, [
        "jobTitle", "occupationTitle", "jobOrderTitle", "occupation",
        "socOccupationalTitle"
    ])

    employer = first_value(f, [
        "employerName", "employerLegalName", "businessName",
        "employerBusinessName", "companyName"
    ])

    city = first_value(f, ["city", "worksiteCity", "placeOfEmploymentCity"])
    state = first_value(f, ["state", "stateCode", "worksiteState", "placeOfEmploymentState"])
    wage = first_value(f, [
        "wageRate", "offeredWage", "wage", "payRate", "hourlyWage",
        "wageAmount"
    ])
    start = first_value(f, ["beginDate", "startDate", "workStartDate", "firstDateOfWork"])
    end = first_value(f, ["endDate", "workEndDate", "lastDateOfWork"])
    workers = first_value(f, [
        "numberOfWorkersRequested", "workersRequested", "totalWorkers",
        "numberWorkers"
    ])
    email = clean_email(first_value(f, [
        "emailAddressToApply", "emailToApply", "applicationEmail",
        "recruitmentEmail", "employerEmail", "businessEmailAddress",
        "emailAddress"
    ]))
    phone = clean_phone(first_value(f, [
        "telephoneNumberToApply", "phoneNumberToApply", "applicationPhone",
        "recruitmentPhone", "employerPhone", "telephoneNumber",
        "phoneNumber", "telephone"
    ]))
    apply_url = clean_url(first_value(f, [
        "websiteAddressToApply", "webAddressToApply", "applicationUrl",
        "applicationURL", "applyUrl", "websiteUrl"
    ]))

    # Determine visa from case number and available form/application fields.
    all_text = " ".join(str(v) for v in f.values()).upper()
    if "H-2B" in case_no.upper() or "9142B" in all_text or "H2B" in all_text:
        visa = "H-2B"
    else:
        visa = "H-2A" if ("H-2A" in case_no.upper() or "790A" in all_text or "9142A" in all_text or "H2A" in all_text) else ""

    job_url = f"{SITE_BASE}/jobs/{quote(case_no)}" if case_no else ""

    return {
        "Visa": visa,
        "Job title": str(title).strip(),
        "Employer": str(employer).strip(),
        "City": str(city).strip(),
        "State": str(state).strip(),
        "Wage": str(wage).strip(),
        "Start": str(start).strip(),
        "End": str(end).strip(),
        "Workers": str(workers).strip(),
        "Application email": email,
        "Application phone": phone,
        "Apply website": apply_url,
        "Case number": case_no,
        "DOL job link": job_url,
    }

def make_dataframe(records):
    rows = [record_to_row(r) for r in records]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # Remove rows that clearly aren't job orders.
    df = df[(df["Job title"] != "") | (df["Employer"] != "") | (df["Case number"] != "")]
    return df.drop_duplicates(subset=["Case number", "Employer", "Job title"]).reset_index(drop=True)

def parse_wage(x):
    m = re.search(r"(\d+(?:\.\d+)?)", str(x))
    return float(m.group(1)) if m else None

def get_detail_fallback(case_no):
    """Fetch a DOL job page when the feed omitted recruitment information."""
    if not case_no:
        return "", ""
    try:
        url = f"{SITE_BASE}/jobs/{quote(case_no)}"
        html = requests.get(url, timeout=TIMEOUT).text
        email = ""
        phone = ""
        m = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', html, re.I)
        if m:
            email = m.group(0)
        # Prefer a US-style number.
        m = re.search(r'(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}', html)
        if m:
            phone = m.group(0)
        return clean_email(email), clean_phone(phone)
    except Exception:
        return "", ""

st.title("🇺🇸 US Seasonal Jobs Finder")
st.caption("Searches the U.S. Department of Labor SeasonalJobs.gov data feeds and surfaces recruitment contacts.")

with st.sidebar:
    st.header("Filters")
    visa_filter = st.multiselect("Visa program", ["H-2A", "H-2B"], default=["H-2A"])
    keyword = st.text_input("Keyword", placeholder="farmworker, harvest, construction…")
    state_filter = st.text_input("State", placeholder="TX, WA, Idaho…")
    min_wage = st.number_input("Minimum hourly wage ($)", min_value=0.0, value=0.0, step=0.50)
    only_activeish = st.checkbox("Prefer jobs with future end dates", value=True)
    need_contact = st.checkbox("Only show jobs with email or phone", value=True)

    st.divider()
    st.markdown("**Data source**")
    st.write("U.S. Department of Labor SeasonalJobs.gov")
    st.caption("The DOL says these feeds are updated daily and are intended for third-party job-search sites.")

if "df" not in st.session_state:
    st.session_state.df = None
    st.session_state.feed_info = None

if st.button("🔄 Refresh DOL jobs", type="primary", use_container_width=True):
    st.session_state.df = None

if st.session_state.df is None:
    with st.spinner("Downloading the latest DOL job feed…"):
        try:
            content, url, used_date = load_latest_feed("jo")
            records, filename = parse_zip_json(content)
            df = make_dataframe(records)
            st.session_state.df = df
            st.session_state.feed_info = (used_date, url, filename, len(records))
        except Exception as e:
            st.error("The DOL feed could not be loaded.")
            st.code(str(e))
            st.stop()

df = st.session_state.df.copy()
used_date, feed_url, filename, raw_count = st.session_state.feed_info

# Enrich missing recruitment contacts lazily only for displayed rows.
if visa_filter:
    df = df[df["Visa"].isin(visa_filter)]

if keyword:
    q = keyword.lower()
    mask = (
        df["Job title"].str.lower().str.contains(q, na=False)
        | df["Employer"].str.lower().str.contains(q, na=False)
    )
    df = df[mask]

if state_filter:
    df = df[df["State"].str.lower().str.contains(state_filter.lower(), na=False)]

df["WageNumeric"] = df["Wage"].map(parse_wage)
if min_wage > 0:
    df = df[df["WageNumeric"].fillna(-1) >= min_wage]

if only_activeish:
    today = pd.Timestamp.today().normalize()
    parsed_end = pd.to_datetime(df["End"], errors="coerce")
    df = df[parsed_end.isna() | (parsed_end >= today)]

if need_contact:
    df = df[(df["Application email"] != "") | (df["Application phone"] != "")]

df = df.sort_values(["WageNumeric", "Start"], ascending=[False, True], na_position="last")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Jobs shown", len(df))
c2.metric("Employers", df["Employer"].replace("", pd.NA).nunique())
c3.metric("H-2A", int((df["Visa"] == "H-2A").sum()))
c4.metric("H-2B", int((df["Visa"] == "H-2B").sum()))

st.info(
    f"Feed date: **{used_date}** • Records read: **{raw_count:,}** • "
    f"Source: U.S. Department of Labor. Visa badge means the DOL record is an H-2A/H-2B job order; "
    f"it does **not** by itself guarantee that a particular foreign applicant will receive a visa."
)

if df.empty:
    st.warning("No jobs match the current filters.")
    st.stop()

# User-facing table
display_cols = [
    "Visa", "Job title", "Employer", "City", "State", "Wage",
    "Start", "End", "Workers", "Application email", "Application phone",
    "Case number", "DOL job link"
]
st.dataframe(
    df[display_cols],
    use_container_width=True,
    hide_index=True,
    column_config={
        "DOL job link": st.column_config.LinkColumn("DOL job", display_text="Open job"),
        "Apply website": st.column_config.LinkColumn("Apply website"),
    },
)

st.subheader("Job details & application")
selected_case = st.selectbox(
    "Choose a job",
    df["Case number"].tolist(),
    format_func=lambda x: (
        df.loc[df["Case number"] == x, "Job title"].iloc[0]
        + " — "
        + df.loc[df["Case number"] == x, "Employer"].iloc[0]
    ),
)

row = df[df["Case number"] == selected_case].iloc[0]

# Fallback contact lookup if feed data omitted it.
email = row["Application email"]
phone = row["Application phone"]
if not email or not phone:
    if st.button("Find missing recruitment contact from DOL job page"):
        with st.spinner("Checking the DOL job page…"):
            e2, p2 = get_detail_fallback(selected_case)
            email = email or e2
            phone = phone or p2
            st.success("Checked the DOL job page.")
else:
    st.caption("Recruitment contact supplied by the DOL feed.")

a, b, c = st.columns(3)
with a:
    st.markdown(f"### {row['Job title'] or 'Job'}")
    st.write(f"**Employer:** {row['Employer']}")
    st.write(f"**Location:** {row['City']}, {row['State']}")
with b:
    st.write(f"**Visa program:** {row['Visa'] or 'Not identified'}")
    st.write(f"**Wage:** {row['Wage'] or 'Not listed'}")
    st.write(f"**Workers requested:** {row['Workers'] or 'Not listed'}")
with c:
    st.write(f"**Start:** {row['Start'] or 'Not listed'}")
    st.write(f"**End:** {row['End'] or 'Not listed'}")
    if row["DOL job link"]:
        st.link_button("Open official DOL job", row["DOL job link"], use_container_width=True)

st.markdown("#### Recruitment contact")
r1, r2 = st.columns(2)
with r1:
    if email:
        st.markdown(f"📧 **Email:** `{email}`")
        st.link_button("✉️ Open email", f"mailto:{email}", use_container_width=True)
    else:
        st.warning("No application email found in the DOL record.")
with r2:
    if phone:
        st.markdown(f"📞 **Phone:** `{phone}`")
        st.link_button("📞 Call", f"tel:{phone}", use_container_width=True)
    else:
        st.warning("No application phone found in the DOL record.")

if row["Apply website"]:
    st.link_button("🌐 Employer application website", row["Apply website"])

st.divider()
st.subheader("Export")
csv = df[display_cols].to_csv(index=False).encode("utf-8")
st.download_button(
    "⬇️ Download filtered jobs as CSV",
    csv,
    "seasonal_jobs_filtered.csv",
    "text/csv",
    use_container_width=True,
)

with st.expander("How this app determines visa support"):
    st.write(
        "H-2A is identified from the DOL agricultural job-order/application data; H-2B is identified "
        "from the corresponding H-2B data. The app reports the program attached to the DOL record. "
        "It does not claim that a worker is personally eligible, that an employer will sponsor a specific "
        "applicant, or that a visa will be issued."
    )

with st.expander("Source and technical notes"):
    st.write(f"Latest feed URL used: {feed_url}")
    st.write(f"Archive member read: {filename}")
    st.write(
        "This app uses the public DOL data feed rather than scraping a search-results page. "
        "That is more stable and is explicitly intended by DOL for third-party job-search sites."
    )
