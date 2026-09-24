
# US Seasonal Jobs Finder

A Streamlit web app for finding U.S. seasonal/temporary jobs from the U.S. Department of Labor's SeasonalJobs.gov data feeds.

## What it does

- Pulls the latest public DOL 790/790A job-order feed.
- Identifies H-2A/H-2B records where possible.
- Shows:
  - employer
  - job title
  - city/state
  - wage
  - start/end dates
  - workers requested
  - application email
  - application phone
  - DOL job link
  - employer application website when supplied
- Filters by visa program, keyword, state and minimum wage.
- Provides click-to-email and click-to-call buttons.
- Exports the filtered list to CSV.
- Can check an individual DOL job page when recruitment contact fields are missing.

## Run locally

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Deploy

This can be deployed to Streamlit Community Cloud, Render, Railway, or another Python host.

## Important

The DOL feed identifies the visa program associated with the job order. That is not a guarantee that an individual applicant is eligible for a visa or that the employer will hire a particular foreign applicant.

The DOL states that its data feeds are updated daily and are intended to allow third-party job-search sites to index seasonal/temporary opportunities.
