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
TIMEOUT = 60

# DOL publishes three feeds. "jo" = 790/790A (H-2A farm job orders),
# "h2a" = 9142A (H-2A applications), "h2b" = 9142B (H-2B non-farm: construction,
# landscaping, hospitality, etc.). H-2B jobs are ONLY in the "h2b" feed.
FEEDS = {"jo": "H-2A", "h2a": "H-2A", "h2b": "H-2B"}


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def download_feed(feed_kind: str, feed_date: str):
    url = f"{FEED_BASE}/{feed_kind}/{feed_date}"
    r = requests.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.content, url


def load_latest_feed(kind):
    errors = []
    today = date.today()
    for i in range(0, 8):
        d = today - timedelta(days=i)
        try:
            content, url = download_feed(kind, d.isoformat())
            return content, url, d.isoformat()
        except Exception as e:
            errors.append(f"{d.isoformat()}: {e}")
    raise RuntimeError(f"Could not download the '{kind}' feed.\n" + "\n".join(errors))


def find_json_objects(obj):
    if isinstance(obj, list):
        for x in obj:
            yield x
    elif isinstance(obj, dict):
        for key in ("data", "results", "jobs", "records", "items", "jobOrders", "cases"):
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
        name = max(json_names, key=lambda n: z.getinfo(n).file_size)
        raw = z.read(name).decode("utf-8-sig")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Some feeds are one JSON object per line.
        data = [json.loads(line) for line in raw.splitlines() if line.strip()]
    return list(find_json_objects(data)), name


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


def _empty(v):
    return v is None or (isinstance(v, str) and not v.strip())


def first_value(flat, aliases):
    aliases = [norm(a) for a in aliases]
    for a in aliases:
        for k, v in flat.items():
            if k == a and not _empty(v):
                return v
    for a in aliases:
        for k, v in flat.items():
            if k.endswith(a) and not _empty(v):
                return v
    return ""


def pick(flat, aliases, fuzzy=(), exclude=()):
    """Try known names first, then fall back to any field whose name contains
    all the given words (names are lower-case letters/digits only)."""
    v = first_value(flat, aliases)
    if not _empty(v):
        return v
    for words in fuzzy:
        for k, val in flat.items():
            if _empty(val):
                continue
            if all(w in k for w in words) and not any(x in k for x in exclude):
                return val
    return ""


def normalize_case_number(v):
    """DOL job pages use the ETA case number, e.g. H-300-26265-251804.
    Job-order numbers look like JO-A-300-26265-251804 and give 'case not found'
    on the website, so rebuild the H- form."""
    s = str(v or "").strip()
    m = re.search(r"(\d{3})-(\d{5})-(\d{6})", s)
    return f"H-{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else s


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


def record_to_row(rec, kind):
    f = flatten(rec)

    case_no = normalize_case_number(pick(
        f,
        ["caseNumber", "caseNo", "caseID", "caseId", "jobOrderCaseNumber",
         "jobOrderNumber", "h2aCaseNumber", "h2bCaseNumber"],
        fuzzy=[("casenumber",), ("jobordernumber",), ("caseno",)],
    ))
    if not case_no.startswith("H-"):
        for v in f.values():
            if re.search(r"\d{3}-\d{5}-\d{6}", str(v)):
                case_no = normalize_case_number(v)
                break

    title = pick(
        f,
        ["jobTitle", "occupationTitle", "jobOrderTitle", "occupation", "socOccupationalTitle"],
        fuzzy=[("jobtitle",), ("occupation", "title"), ("title",)],
        exclude=("soc",),
    )
    employer = pick(
        f,
        ["employerName", "employerLegalName", "businessName",
         "employerBusinessName", "companyName", "legalBusinessName"],
        fuzzy=[("employer", "name"), ("business", "name"), ("legal", "name")],
        exclude=("contact", "agent", "attorney", "preparer"),
    )
    city = pick(
        f, ["city", "worksiteCity", "placeOfEmploymentCity"],
        fuzzy=[("worksite", "city"), ("city",)],
    )
    state = pick(
        f, ["state", "stateCode", "worksiteState", "placeOfEmploymentState"],
        fuzzy=[("worksite", "state"), ("state",)],
        exclude=("statement", "status"),
    )
    wage = pick(
        f, ["wageRate", "offeredWage", "wage", "payRate", "hourlyWage", "wageAmount",
            "basicRate", "basicRateFrom"],
        fuzzy=[("wage",), ("payrate",), ("basicrate",)],
    )
    start = pick(
        f, ["beginDate", "startDate", "workStartDate", "firstDateOfWork", "employmentBeginDate"],
        fuzzy=[("begin", "date"), ("start", "date")],
    )
    end = pick(
        f, ["endDate", "workEndDate", "lastDateOfWork", "employmentEndDate"],
        fuzzy=[("end", "date")],
    )
    workers = pick(
        f, ["numberOfWorkersRequested", "workersRequested", "totalWorkers", "numberWorkers",
            "totalWorkersNeeded"],
        fuzzy=[("workers", "needed"), ("workers", "requested"), ("number", "workers"), ("workers",)],
    )
    email = clean_email(pick(
        f, ["emailAddressToApply", "emailToApply", "applicationEmail", "recruitmentEmail",
            "employerEmail", "businessEmailAddress", "emailAddress"],
        fuzzy=[("email",)],
    ))
    if not email:
        for v in f.values():
            e = clean_email(v) if isinstance(v, str) else ""
            if e:
                email = e
                break
    phone = clean_phone(pick(
        f, ["telephoneNumberToApply", "phoneNumberToApply", "applicationPhone",
            "recruitmentPhone", "employerPhone", "telephoneNumber", "phoneNumber", "telephone"],
        fuzzy=[("phone",), ("telephone",)],
        exclude=("fax",),
    ))
    apply_url = clean_url(pick(
        f, ["websiteAddressToApply", "webAddressToApply", "applicationUrl", "applicationURL",
            "applyUrl", "websiteUrl"],
        fuzzy=[("website",), ("url",)],
    ))

    # Visa comes from WHICH FEED the record was in (most reliable), then the case number.
    visa = FEEDS.get(kind, "")
    if not visa:
        if case_no.startswith("H-400"):
            visa = "H-2B"
        elif case_no.startswith("H-300"):
            visa = "H-2A"

    job_url = f"{SITE_BASE}/jobs/{quote(case_no)}" if case_no.startswith("H-") else ""

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


def _first_nonempty(series):
    for x in series:
        if x not in ("", None):
            return x
    return ""


def make_dataframe(kind_records):
    rows = [record_to_row(r, k) for k, r in kind_records]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df[(df["Job title"] != "") | (df["Employer"] != "") | (df["Case number"] != "")]
    cols = list(df.columns)
    keyed = df[df["Case number"] != ""]
    rest = df[df["Case number"] == ""].drop_duplicates(subset=["Employer", "Job title"])
    # The same case can appear in two feeds; merge them, keeping any non-empty value.
    merged = keyed.groupby("Case number", as_index=False, sort=False).agg(_first_nonempty)
    return pd.concat([merged[cols], rest[cols]], ignore_index=True)


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
        m = re.search(r'(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}', html)
        if m:
            phone = m.group(0)
        return clean_email(email), clean_phone(phone)
    except Exception:
        return "", ""


def load_everything():
    kind_records, debug, errors = [], [], []
    for kind in FEEDS:
        try:
            content, url, used = load_latest_feed(kind)
            recs, fname = parse_zip_json(content)
            kind_records.extend((kind, r) for r in recs)
            debug.append({
                "feed": kind, "date": used, "url": url, "file": fname, "records": len(recs),
                "keys": list(flatten(recs[0]).keys())[:80] if recs else [],
            })
        except Exception as e:
            errors.append(f"{kind}: {e}")
    if not kind_records:
        raise RuntimeError("\n".join(errors) or "No records found in any feed.")
    return kind_records, debug, errors


st.title("🇺🇸 US Seasonal Jobs Finder")
st.caption("Searches the U.S. Department of Labor SeasonalJobs.gov data feeds and surfaces recruitment contacts.")

with st.sidebar:
    st.header("Filters")
    visa_filter = st.multiselect("Visa program", ["H-2A", "H-2B", "Unknown"], default=["H-2A", "H-2B", "Unknown"])
    keyword = st.text_input("Keyword", placeholder="tile, construction, farm, harvest…")
    state_filter = st.text_input("State", placeholder="TX, WA, Idaho…")
    min_wage = st.number_input("Minimum hourly wage ($)", min_value=0.0, value=0.0, step=0.50)
    only_activeish = st.checkbox("Prefer jobs with future end dates", value=True)
    need_contact = st.checkbox("Only show jobs with email or phone", value=False)

    st.divider()
    st.markdown("**Data source**")
    st.write("U.S. Department of Labor SeasonalJobs.gov")
    st.caption("The DOL says these feeds are updated daily and are intended for third-party job-search sites.")

if "df" not in st.session_state:
    st.session_state.df = None

if st.button("🔄 Refresh DOL jobs", type="primary", use_container_width=True):
    st.session_state.df = None
    st.cache_data.clear()

if st.session_state.df is None:
    with st.spinner("Downloading the latest DOL job feeds (H-2A and H-2B)…"):
        try:
            kind_records, debug, errors = load_everything()
            st.session_state.df = make_dataframe(kind_records)
            st.session_state.debug = debug
            st.session_state.errors = errors
            st.session_state.raw_count = len(kind_records)
        except Exception as e:
            st.error("The DOL feeds could not be loaded.")
            st.code(str(e))
            st.stop()

full = st.session_state.df
df = full.copy()
raw_count = st.session_state.raw_count

for err in st.session_state.get("errors", []):
    st.warning("A feed failed to load: " + err)

stages = [("Records read from all feeds", raw_count), ("Jobs after cleaning & merging", len(df))]

if visa_filter:
    df = df[df["Visa"].replace("", "Unknown").isin(visa_filter)]
stages.append(("After visa filter", len(df)))

if keyword:
    q = keyword.lower()
    mask = (
        df["Job title"].str.lower().str.contains(q, na=False)
        | df["Employer"].str.lower().str.contains(q, na=False)
    )
    df = df[mask]
stages.append(("After keyword filter", len(df)))

if state_filter:
    df = df[df["State"].str.lower().str.contains(state_filter.lower(), na=False)]
stages.append(("After state filter", len(df)))

df["WageNumeric"] = df["Wage"].map(parse_wage)
if min_wage > 0:
    df = df[df["WageNumeric"].fillna(-1) >= min_wage]
stages.append(("After wage filter", len(df)))

if only_activeish:
    today = pd.Timestamp.today().normalize()
    parsed_end = pd.to_datetime(df["End"], errors="coerce")
    df = df[parsed_end.isna() | (parsed_end >= today)]
stages.append(("After end-date filter", len(df)))

if need_contact:
    df = df[(df["Application email"] != "") | (df["Application phone"] != "")]
stages.append(("After contact filter", len(df)))

with st.expander("🛠 Debug: what did the feeds contain?"):
    for name, n in stages:
        st.write(f"{name}: **{n:,}**")
    st.write("Jobs per visa:", full["Visa"].replace("", "Unknown").value_counts().to_dict())
    st.write(
        "Jobs with email:", int((full["Application email"] != "").sum()),
        "| with phone:", int((full["Application phone"] != "").sum()),
        "| with job title:", int((full["Job title"] != "").sum()),
        "| with employer:", int((full["Employer"] != "").sum()),
        "| with state:", int((full["State"] != "").sum()),
        "| with wage:", int((full["Wage"] != "").sum()),
    )
    for d in st.session_state.get("debug", []):
        st.markdown(f"**Feed `{d['feed']}`** — file date {d['date']}, {d['records']:,} records")
        st.code("\n".join(d["keys"]) or "(no records)")
    st.dataframe(full.head(5))

df = df.sort_values(["WageNumeric", "Start"], ascending=[False, True], na_position="last")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Jobs shown", len(df))
c2.metric("Employers", df["Employer"].replace("", pd.NA).nunique())
c3.metric("H-2A", int((df["Visa"] == "H-2A").sum()))
c4.metric("H-2B", int((df["Visa"] == "H-2B").sum()))

feed_dates = ", ".join(f"{d['feed']}: {d['date']}" for d in st.session_state.get("debug", []))
st.info(
    f"Feed dates: **{feed_dates}** • Records read: **{raw_count:,}** • "
    f"Source: U.S. Department of Labor. The visa badge means the DOL record is an H-2A/H-2B job order; "
    f"it does **not** by itself guarantee that a particular foreign applicant will receive a visa."
)
st.warning(
    "⚠️ Scam warning: real H-2A/H-2B employers do NOT charge workers recruitment fees. "
    "Never pay anyone for a job offer. Apply only through the contacts on the official DOL listing."
)

if df.empty:
    st.warning("No jobs match the current filters. Open the Debug section above to see where they were filtered out.")
    st.stop()

display_cols = [
    "Visa", "Job title", "Employer", "City", "State", "Wage",
    "Start", "End", "Workers", "Application email", "Application phone",
    "Case number", "DOL job link",
]
st.dataframe(
    df[display_cols],
    use_container_width=True,
    hide_index=True,
    column_config={
        "DOL job link": st.column_config.LinkColumn("DOL job", display_text="Open job"),
    },
)

st.subheader("Job details & application")
case_options = df["Case number"].tolist()
label_by_case = {
    c: f"{(t or 'Job')} — {e}" for c, t, e in zip(df["Case number"], df["Job title"], df["Employer"])
}
selected_case = st.selectbox("Choose a job", case_options, format_func=lambda x: label_by_case.get(x, x))

row = df[df["Case number"] == selected_case].iloc[0]

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
        "H-2A jobs come from the DOL 790/790A and 9142A feeds (farm work). H-2B jobs come from the "
        "9142B feed (non-farm work such as construction, landscaping and hospitality). The app reports "
        "the program attached to the DOL record. It does not claim that a worker is personally eligible, "
        "that an employer will sponsor a specific applicant, or that a visa will be issued."
    )
