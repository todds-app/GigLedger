<div align="center">

# 💰 GigLedger

### The All-in-One Finance Dashboard for Freelancers

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.1-000000?style=flat-square&logo=flask&logoColor=white)](https://flask.palletsprojects.com)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)

</div>

---

> **Stop juggling multiple apps.** GigLedger is a self-hosted finance dashboard built for freelancers, contractors, and gig workers who want a clear picture of income, expenses, invoices, projects, and quarterly tax obligations — all in one place, without the complexity of full accounting software.

---

## ✨ Why GigLedger?

Freelancers have unique financial challenges that personal finance apps don't solve and accounting software overcomplicates. GigLedger fills that gap with three core questions answered at a glance:

| Question | Answer |
|---|---|
| **How much can I safely spend?** | **Safe to Spend** = Bank Balance − Tax Obligation |
| **What do I owe this quarter?** | **Quarterly Tax Estimator** = Net Income × Tax Rate |
| **How long can I survive?** | **Runway Calculator** = Balance ÷ Avg Monthly Expenses |

No more setting aside random percentages. No more spreadsheet gymnastics. Just clarity.

---

## 🚀 Features

### 📊 Smart Dashboard
- **4 Hero Cards** — Monthly Income, Monthly Expenses, Safe to Spend, Tax Reserve
- **Trend Indicators** — Month-over-month % change at a glance
- **6-Month Bar Chart** — Income vs. Expenses trend (Chart.js)
- **Expense Breakdown** — Doughnut chart by category for the current month
- **Quick Add** — Add income or expenses right from the dashboard
- **Runway Calculator** — Color-coded survival months (green ≥6, yellow ≥3, red <3)
- **Bank Balance & Net This Month** — Instant financial health check
- **Monthly Commitments** — Total from active recurring expenses
- **Savings Goals Progress** — Visual progress bars with color-coded status
- **Active Projects** — Quick view of ongoing work with earnings
- **Outstanding Invoices** — Unpaid invoice summary with overdue warnings
- **Recent Transactions** — Last 5 transactions at a glance

### 💸 Transactions
- **Full transaction table** with filter bar (type, category, month, year)
- **Tax Impact column** — See how each transaction affects your tax burden
- **Deductible highlighting** — Green rows for tax-deductible expenses
- **Summary bar** — Total Income, Expenses, Deductible amount, Tax Saving, Net
- **Add Transaction modal** — Dynamic categories, real-time tax savings preview
- **Edit & Delete** — Full CRUD with inline actions
- **Export to PDF or CSV** — Filtered reports with full detail and summary rows
- **Auto-Posted from Invoices** — Income and tax reserve transactions created automatically when invoices are paid

### 🧾 Invoicing
- **Create professional invoices** with line items, quantities, and rates
- **Auto-calculated tax** — Applied based on your configured tax rate
- **Client billing** — Link invoices to clients with full contact info
- **Invoice number auto-generation** — Configurable prefix (default: INV-0001)
- **Status workflow** — Draft → Sent → Paid / Overdue / Cancelled
- **One-click status updates** — Mark as sent, paid, overdue, or cancel
- **Auto-post payment** — Marking an invoice as paid automatically creates:
  - An income transaction for the invoice total
  - A tax reserve expense transaction for the tax portion
- **Revert support** — Un-paying an invoice removes the auto-posted transactions
- **PDF download** — Professional invoice PDF via WeasyPrint (or HTML fallback)
- **Print-friendly** — Clean print layout for physical copies
- **Invoice detail view** — Full preview with line items, totals, and client info

### 👥 Client Management
- **Full client CRM** — Name, email, phone, company, address, notes
- **Active/inactive toggle** — Hide clients you're no longer working with
- **Client detail view** — Total invoiced, total paid, outstanding balance
- **Linked to invoices & projects** — Full relationship tracking
- **8 demo clients** — Pre-loaded with realistic data

### 📁 Project Tracking
- **Project dashboard** — All projects with status, client, and earnings
- **Rate types** — Hourly, fixed, or daily rate support
- **Hours logging** — Track time with auto-earning calculation
- **Status workflow** — Active, completed, on hold, cancelled
- **Deadline tracking** — Visual deadline indicators
- **Color-coded** — Custom colors per project
- **Create transactions from hours** — Optional: auto-create income when logging hours
- **7 demo projects** — Showing various states and rate types

### 🎯 Savings Goals
- **Visual progress bars** — Color-coded with percentage complete
- **Target amounts** — Set a goal and track progress
- **Deadlines** — Know when you need to reach your target
- **Mark as completed** — Celebrate when you hit your goal
- **5 demo goals** — Emergency fund, laptop, vacation, tax reserve, conference

### 🔄 Recurring Transactions
- **Schedule recurring expenses** — Weekly, monthly, quarterly, or yearly
- **Auto-generated on schedule** — One-click generate for due items
- **Day of month control** — Specify which day the charge hits
- **Active/pause toggle** — Temporarily disable without deleting
- **Tax deductible flag** — Mark recurring expenses as deductible
- **7 demo recurring items** — Figma, AWS, co-working, internet, Adobe, domain, LinkedIn

### 🧾 Quarterly Tax Estimator
- **Q1–Q4 breakdown cards** — Income, Deductions, Net Income, Estimated Tax per quarter
- **Current quarter highlight** with underfunded warnings
- **One-click recalculate** — Persists estimates to the database
- **"How taxes are calculated" explainer** — Transparent methodology section
- **Auto tax reserve** — Invoice payments auto-set-aside the tax portion

### 📊 Reports & Analytics
- **Yearly financial report** — Full income and expense breakdown by month
- **Monthly bar chart** — Visual trend of income vs expenses
- **Category breakdown** — Where your money goes
- **Exportable data** — CSV and PDF exports from transactions

### ⚙️ Settings
- **Tax Rate** — Custom percentage with quick presets (25%, 30%, 35%)
- **Currency** — 7 supported: USD, EUR, GBP, CAD, AUD, INR, JPY
- **Custom Categories** — Add, remove, or reset income and expense categories
- **Business Profile** — Name, address, phone for invoice headers
- **Invoice Settings** — Custom prefix, default notes
- **5 Color Themes** — Emerald, Ocean, Sunset, Rose, Midnight
- **Dark Mode** — Full dark mode with CSS custom properties
- **Profile** — Email and member-since display

### 🔐 Authentication
- Secure login with Flask-Login + Flask-Bcrypt
- "Remember me" session support
- Demo account pre-loaded with 6 months of realistic data

---

## 🧠 Core Algorithms

### 1. Safe to Spend
```
Safe Balance = Bank Balance − Total Tax Obligation
```
Your bank balance minus every dollar you already owe in taxes this year. This is the number you can actually spend without getting into trouble. The tax obligation is computed quarterly: for each quarter, if your net income (income minus deductible expenses) is positive, you owe `net income × tax rate`.

### 2. Quarterly Tax Estimator
```
Quarterly Net Income = Income − Deductible Expenses
Estimated Tax = max(0, Quarterly Net Income × Tax Rate)
```
Only expenses marked as **tax-deductible** reduce your taxable income. The app calculates this automatically per quarter and shows the real-time impact on every transaction. When an invoice is marked as paid, the tax portion is automatically set aside as a separate "Tax Reserve" expense transaction.

### 3. Runway Calculator
```
Runway (months) = Current Balance ÷ Average Monthly Expenses (last 3 months)
```
Tells you how many months you can survive at your current spending rate. Color-coded for instant awareness: green for 6+ months, yellow for 3-6 months, red for under 3 months.

### 4. Auto Tax Reserve (New)
```
When invoice is paid:
  → Income transaction: +invoice.total
  → Tax reserve transaction: -invoice.tax_amount
```
When you mark an invoice as paid, GigLedger automatically creates two transactions: the income you received (the full invoice total including tax), and a tax reserve expense (the tax portion) that sets aside what you owe. This ensures your "Safe to Spend" accurately reflects that the tax money is already spoken for. If you revert an invoice back to "Sent", both auto-posted transactions are removed.

---

## 🛠 Tech Stack

| Layer | Technology | Why |
|---|---|---|
| **Backend** | Flask 3.1 | Lightweight, Pythonic, perfect for solo-dev tools |
| **Database** | SQLite via SQLAlchemy | Zero-config, portable, great for self-hosted |
| **Auth** | Flask-Login + Flask-Bcrypt | Secure session management with hashed passwords |
| **Templates** | Jinja2 | Server-rendered, fast, no build step |
| **Styling** | TailwindCSS (CDN) | Utility-first, no CSS files to manage |
| **Charts** | Chart.js 4.4 | Beautiful bar + doughnut charts |
| **PDF Export** | WeasyPrint | Professional-quality PDF reports and invoices |
| **CSV Export** | Python csv + StringIO | Lightweight, no extra dependencies |

---

## 📦 Quick Start

### Prerequisites
- Python 3.11 or higher
- pip

### Installation

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/gigledger.git
cd gigledger

# Create a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r freelancecash/requirements.txt

# Run the app
python freelancecash/run.py
```

The app starts at **http://localhost:3030**

### Demo Account

Log in instantly with pre-loaded data:

| Field | Value |
|---|---|
| **Email** | `demo@freelancecash.com` |
| **Password** | `demo1234` |

The demo account includes a complete freelancer workspace:
- **~55 transactions** across 6 months (income, deductible expenses, non-deductible expenses, auto-posted invoice payments, and tax reserve entries)
- **8 clients** with full contact info
- **7 projects** in various states (active, completed, on hold)
- **8 invoices** (4 paid with auto-posted income + tax reserve, 2 sent, 1 draft, 1 overdue)
- **5 savings goals** with progress tracking
- **7 recurring transactions** (monthly subscriptions and yearly renewals)
- All invoices include proper tax calculations at the 30% rate

---

## 📁 Project Structure

```
gigledger/
├── freelancecash/
│   ├── app.py                  # Flask app factory, config, seed data, DB migrations
│   ├── models.py               # SQLAlchemy models (8 models)
│   ├── finance.py              # Core financial calculation engine
│   ├── run.py                  # Entry point (port 3030)
│   ├── requirements.txt        # Python dependencies
│   ├── freelancecash.db        # SQLite database (auto-created)
│   ├── routes/
│   │   ├── auth.py             # Login / Signup / Logout
│   │   ├── dashboard.py        # Main dashboard + Quick Add
│   │   ├── transactions.py     # CRUD + CSV/PDF export
│   │   ├── invoices.py         # Invoice CRUD + PDF + mark-as-paid + auto-post
│   │   ├── clients.py          # Client CRM with detail view
│   │   ├── projects.py         # Project tracking + hours logging
│   │   ├── goals.py            # Savings goals with progress
│   │   ├── recurring.py        # Recurring transaction scheduling
│   │   ├── taxes.py            # Quarterly tax estimator
│   │   ├── reports.py          # Yearly financial reports
│   │   └── settings.py         # Tax rate, currency, categories, themes, dark mode
│   └── templates/
│       ├── base.html           # Layout: sidebar + mobile nav + themes + dark mode
│       ├── auth/
│       │   ├── login.html
│       │   └── signup.html
│       ├── dashboard/
│       │   └── index.html      # Full dashboard with hero cards, charts, Quick Add
│       ├── transactions/
│       │   └── index.html      # Transaction table + filters + export
│       ├── invoices/
│       │   ├── index.html      # Invoice list with status filters
│       │   ├── create.html     # Invoice creation form
│       │   └── detail.html     # Invoice preview with actions
│       ├── clients/
│       │   ├── index.html      # Client list
│       │   └── detail.html     # Client detail with invoicing stats
│       ├── projects/
│       │   └── index.html      # Project dashboard
│       ├── goals/
│       │   └── index.html      # Savings goals tracker
│       ├── recurring/
│       │   └── index.html      # Recurring transaction manager
│       ├── taxes/
│       │   └── index.html      # Quarterly tax cards
│       ├── reports/
│       │   └── index.html      # Yearly financial report
│       └── settings/
│           └── index.html      # Profile, tax rate, currency, categories, themes
└── README.md
```

---

## 🗃 Database Models

| Model | Table | Key Fields |
|---|---|---|
| **User** | `users` | email, password_hash, tax_rate, currency, categories, theme, dark_mode, business info, invoice settings |
| **Transaction** | `transactions` | amount (+income/-expense), date, category, description, is_tax_deductible, source (manual/invoice/project/recurring), invoice_id |
| **Invoice** | `invoices` | client_id, invoice_number, status, issue_date, due_date, subtotal, tax_amount, total, paid_date |
| **InvoiceLineItem** | `invoice_line_items` | invoice_id, description, quantity, rate, amount |
| **Client** | `clients` | name, email, phone, company, address, notes, is_active |
| **Project** | `projects` | client_id, name, description, status, rate_type, rate, hours_logged, deadline, color |
| **Goal** | `goals` | name, target_amount, current_amount, deadline, icon, color, is_completed |
| **RecurringTransaction** | `recurring_transactions` | description, amount, category, frequency, day_of_month, is_active, last_generated, next_date |
| **TaxEstimate** | `tax_estimates` | quarter, year, total_income, total_deductions, estimated_tax_owed |

---

## 🔌 API Endpoints

| Method | Route | Description |
|---|---|---|
| `GET/POST` | `/auth/login` | User login |
| `GET/POST` | `/auth/signup` | User registration |
| `GET` | `/auth/logout` | User logout |
| `GET/POST` | `/dashboard` | Dashboard view + Quick Add |
| `GET` | `/transactions` | Transaction list with filters |
| `POST` | `/transactions/add` | Add new transaction |
| `POST` | `/transactions/edit/<id>` | Edit existing transaction |
| `POST` | `/transactions/delete/<id>` | Delete a transaction |
| `GET` | `/transactions/export/csv` | Export filtered transactions as CSV |
| `GET` | `/transactions/export/pdf` | Export filtered transactions as PDF |
| `GET` | `/invoices` | Invoice list with status filter |
| `GET` | `/invoices/create` | Invoice creation form |
| `POST` | `/invoices/create` | Create invoice with line items |
| `POST` | `/invoices/status/<id>` | Update invoice status (auto-posts transactions on paid) |
| `POST` | `/invoices/delete/<id>` | Delete invoice + linked transactions |
| `GET` | `/invoices/<id>` | Invoice detail view |
| `GET` | `/invoices/<id>/pdf` | Download invoice as PDF |
| `GET` | `/clients` | Client list |
| `POST` | `/clients/add` | Add new client |
| `POST` | `/clients/edit/<id>` | Edit client |
| `POST` | `/clients/toggle/<id>` | Toggle active/inactive |
| `GET` | `/clients/<id>` | Client detail with stats |
| `GET` | `/projects` | Project list |
| `POST` | `/projects/add` | Add new project |
| `POST` | `/projects/log-hours/<id>` | Log hours to project |
| `POST` | `/projects/status/<id>` | Update project status |
| `POST` | `/projects/delete/<id>` | Delete project |
| `GET` | `/goals` | Savings goals list |
| `POST` | `/goals/add` | Add new goal |
| `POST` | `/goals/update/<id>` | Update goal progress |
| `POST` | `/goals/delete/<id>` | Delete goal |
| `GET` | `/recurring` | Recurring transaction list |
| `POST` | `/recurring/add` | Add recurring transaction |
| `POST` | `/recurring/toggle/<id>` | Toggle active/paused |
| `POST` | `/recurring/generate` | Generate due recurring transactions |
| `POST` | `/recurring/delete/<id>` | Delete recurring transaction |
| `GET/POST` | `/taxes` | Quarterly tax breakdown + recalculate |
| `GET` | `/reports` | Yearly financial report |
| `GET/POST` | `/settings` | Tax rate, currency, categories, themes, dark mode, business profile |

---

## 🎨 Themes & Dark Mode

GigLedger ships with **5 color themes** and a full **dark mode** toggle:

| Theme | Accent Color | Vibe |
|---|---|---|
| **Emerald** (default) | Green | Fresh, professional |
| **Ocean** | Blue | Calm, trustworthy |
| **Sunset** | Orange | Warm, energetic |
| **Rose** | Pink | Creative, modern |
| **Midnight** | Purple | Elegant, focused |

Dark mode uses CSS custom properties with `[data-dark="true"]` attribute selectors, ensuring smooth transitions and consistent styling across all themes.

---

## 🔄 Invoice → Transaction Flow

Understanding how invoices and transactions work together:

```
Create Invoice (Draft)
       ↓
Mark as Sent
       ↓
Mark as Paid ──────────────────────┐
       ↓                           ↓
  Auto-create:              Auto-create:
  ✓ Income transaction      ✓ Income transaction (+total)
  (old behavior)            ✓ Tax Reserve expense (-tax_amount)
                                    ↓
                            Both linked via invoice_id
                                    ↓
                     "Safe to Spend" correctly accounts
                     for the tax portion being reserved
```

When an invoice is **un-paid** (reverted to Sent), **both** auto-posted transactions are automatically removed. When an invoice is **deleted**, all linked transactions are also removed. This keeps your books clean and consistent.

---

## 🎯 Roadmap

- [x] ~~**Smart Dashboard** — Hero cards, charts, Quick Add~~
- [x] ~~**Transaction management** — CRUD, filters, export~~
- [x] ~~**Quarterly Tax Estimator** — Per-quarter breakdown~~
- [x] ~~**Invoice generator** — Create, send, mark as paid~~
- [x] ~~**Client management** — Full CRM with stats~~
- [x] ~~**Project tracking** — Hours, rates, deadlines~~
- [x] ~~**Savings goals** — Visual progress tracking~~
- [x] ~~**Recurring transactions** — Auto-generate monthly/weekly~~
- [x] ~~**Reports & Analytics** — Yearly financial reports~~
- [x] ~~**Dark mode** — Because freelancers work at night~~
- [x] ~~**Color themes** — 5 accent color options~~
- [x] ~~**Auto tax reserve** — Invoice payments auto-set-aside tax~~
- [ ] **Bank integration** — Auto-import via Plaid or Open Banking
- [ ] **Multi-currency conversion** — Real-time exchange rates
- [ ] **Receipt upload** — Attach receipts to transactions
- [ ] **Time tracking** — Built-in timer with project integration
- [ ] **Mobile app** — React Native companion
- [ ] **Multi-user / Team** — Shared workspaces for small studios
- [ ] **Email invoicing** — Send invoices directly from the app
- [ ] **Payment reminders** — Auto-notify overdue clients

---

## 🤝 Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

<div align="center">

**Built for freelancers, by a freelancer.**

⭐ Star this repo if it helped you!

</div>
