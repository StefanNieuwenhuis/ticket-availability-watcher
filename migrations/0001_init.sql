CREATE TABLE IF NOT EXISTS listings (
    id TEXT PRIMARY KEY,
    listing_type: TEXT NOT NULL,
    seller TEXT NOT NULL,
    price REAL NO NULL,
    first_seen NUMERIC NOT NULL,
    submitted NUMERIC,
    confirmed NUMERIC
);
CREATE TABLE IF NOT EXISTS email_confirmations (
    id INT PRIMARY KEY,
    verify_url TEXT,
    status TEXT,
    timestamp NUMERIC
);