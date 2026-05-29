# astro-pdf
scrape an astro.com/astrodienst natal chart default PDF

## features
- Data sheets of Type D2GW use a custom font where zodiac signs are rendered as single letters a-l (a = aries ... l = pisces) and retrogrades are boolean flags using '#'
- This script extracts: 
  - natal chart metadata (name, date, place, times)
  - planetary positions (sign, degree, house, declination) & house rulers
  - the aspect grid

## dependencies
pdfplumber (pip3 install pdfplumber)

## usage
    python3 scrape-pdf.py name.pdf [--json out.json]
