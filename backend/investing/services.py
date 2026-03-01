import os
import yfinance as yf
from sec_edgar_downloader import Downloader
from .models import Stock, SECFiling
from django.conf import settings
from datetime import datetime
from pathlib import Path

class SECService:
    def __init__(self, company_name="MyCompany", email="myemail@example.com"):
        # SEC requires user agent info
        self.dl = Downloader(company_name, email, settings.BASE_DIR / "sec_filings")

    def fetch_last_4_filings(self, stock_obj):
        ticker = stock_obj.symbol
        # Fetch 10-K and 10-Q
        for ftype in ["10-K", "10-Q"]:
            self.dl.get(ftype, ticker, limit=4, download_details=True)
        
        # Process downloaded files and save to DB
        filings_dir = settings.BASE_DIR / "sec_filings" / "sec-edgar-filings" / ticker
        
        filings_found = []
        if not filings_dir.exists():
            return filings_found

        for ftype in ["10-K", "10-Q"]:
            type_dir = filings_dir / ftype
            if not type_dir.exists():
                continue
            
            for accession_dir in type_dir.iterdir():
                if not accession_dir.is_dir():
                    continue
                
                accession_number = accession_dir.name
                
                # Check if already in DB
                if SECFiling.objects.filter(accession_number=accession_number).exists():
                    continue
                
                # Find the filing file (usually full-submission.txt or similar)
                # For simplicity, we just save the path to the directory
                filing_path = accession_dir / "full-submission.txt"
                if not filing_path.exists():
                     # Sometimes it's different, let's look for any .txt
                     txt_files = list(accession_dir.glob("*.txt"))
                     if txt_files:
                         filing_path = txt_files[0]
                     else:
                         continue

                # Get filing date from the downloader's metadata or just use current if not easily available
                # In a real app, we'd parse the filing header for the period end date
                # For now, we use a placeholder date or try to parse from directory if possible
                filing_date = datetime.now().date() 

                filing = SECFiling.objects.create(
                    stock=stock_obj,
                    filing_type=ftype,
                    filing_date=filing_date,
                    content_path=str(filing_path),
                    accession_number=accession_number
                )
                filings_found.append(filing)
        
        return filings_found

class DCFService:
    @staticmethod
    def calculate_dcf(ticker):
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            
            # 1. Determine Base Cash Flow (use TTM FCF or Net Income as heuristic)
            # FCF can be temporarily depressed by growth CapEx, so we use max(FCF, Net Income) for growth companies
            fcf = info.get('freeCashflow')
            net_income = info.get('netIncomeToCommon')
            
            # Fallbacks if info is missing
            if fcf is None or net_income is None:
                cf = stock.cashflow
                if not cf.empty:
                    if fcf is None:
                        cfo_keys = ['Cash Flow From Operating Activities', 'Operating Cash Flow']
                        cfo = None
                        for k in cfo_keys:
                            if k in cf.index:
                                cfo = cf.loc[k].iloc[0]
                                break
                        capex = 0
                        capex_keys = ['Capital Expenditure', 'Capital Expenditures']
                        for k in capex_keys:
                            if k in cf.index:
                                capex = abs(cf.loc[k].iloc[0])
                                break
                        if cfo:
                            fcf = cfo - capex
                    
                    if net_income is None:
                        if 'Net Income From Continuing Operations' in cf.index:
                            net_income = cf.loc['Net Income From Continuing Operations'].iloc[0]
            
            # Use max to capture "owners earnings" potential for growth investors
            base_fcf = max(fcf or 0, net_income or 0)
            
            # Last resort fallback to a conservative margin of revenue
            if base_fcf <= 0:
                rev = info.get('totalRevenue', 0)
                if rev:
                    base_fcf = rev * 0.10 # Assume 10% FCF margin as baseline
                else:
                    return {"error": "Insufficient financial data for DCF"}

            # 2. Growth Rate Heuristic
            # Use analyst estimates (capped for conservatism)
            growth_rate = 0.08 # default
            analyst_growth = info.get('earningsGrowth') or info.get('revenueGrowth')
            if analyst_growth and isinstance(analyst_growth, (int, float)):
                # Clip growth between 5% and 20% for first stage
                growth_rate = max(0.05, min(0.20, analyst_growth))
            
            # Growth stock floor (High PE stocks usually have higher growth expectations)
            forward_pe = info.get('forwardPE')
            if forward_pe and forward_pe > 30 and growth_rate < 0.12:
                growth_rate = 0.12

            # 3. Discount Rate (WACC)
            wacc = 0.09 # Standard 9% discount rate
            
            # 4. Terminal Growth Rate
            tg = 0.025 # 2.5% terminal growth
            
            # 5. Project 5 years
            projections_pv = []
            current_fcf = base_fcf
            for i in range(1, 6):
                current_fcf *= (1 + growth_rate)
                # Correct DCF: Discount each future FCF to Present Value
                projections_pv.append(current_fcf / ((1 + wacc) ** i))
            
            pv_fcf_sum = sum(projections_pv)
            
            # 6. Terminal Value (Terminal Value at end of year 5)
            # TV = [FCF_year5 * (1 + tg)] / (wacc - tg)
            tv = (current_fcf * (1 + tg)) / (wacc - tg)
            # PV of Terminal Value (discounted back 5 years once)
            pv_tv = tv / ((1 + wacc) ** 5)
            
            # 7. Enterprise Value (EV)
            enterprise_value = pv_fcf_sum + pv_tv
            
            # 8. Equity Value Adjustment (Net Debt)
            # Equity Value = Enterprise Value + Cash - Debt
            total_cash = info.get('totalCash', 0)
            total_debt = info.get('totalDebt', 0)
            equity_value = enterprise_value + total_cash - total_debt
            
            # 9. Shares Outstanding
            shares = info.get('sharesOutstanding')
            if not shares:
                return {"error": "Shares outstanding not found"}
            
            intrinsic_value_per_share = equity_value / shares
            current_price = info.get('currentPrice')

            return {
                "ticker": ticker,
                "current_price": current_price,
                "intrinsic_value": intrinsic_value_per_share,
                "upside": (intrinsic_value_per_share - current_price) / current_price if current_price else 0,
                "fcf_base": base_fcf,
                "growth_rate_used": growth_rate,
                "wacc_used": wacc,
                "revenue": info.get('totalRevenue'),
                "net_income": info.get('netIncomeToCommon'),
                "research_development": info.get('researchDevelopment'),
                "capital_expenditure": info.get('capitalExpenditure') or (abs(stock.cashflow.loc['Capital Expenditure'].iloc[0]) if 'Capital Expenditure' in stock.cashflow.index else None)
            }
        except Exception as e:
            return {"error": str(e)}

import os
import google.generativeai as genai
from mistralai import Mistral
from django.conf import settings
from asgiref.sync import sync_to_async

import re
from bs4 import BeautifulSoup

def read_filing_content(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
            
        # Strip out embedded multi-media / dense data files from the raw submission
        content = re.sub(r'<XML>.*?</XML>', '', content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r'<GRAPHIC>.*?</GRAPHIC>', '', content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r'<EXCEL>.*?</EXCEL>', '', content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r'<PDF>.*?</PDF>', '', content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r'<ZIP>.*?</ZIP>', '', content, flags=re.DOTALL | re.IGNORECASE)
        
        # Strip out all HTML
        soup = BeautifulSoup(content, 'html.parser')
        text = soup.get_text(separator=' ', strip=True)
        
        # Remove base64 or massive dense unbroken strings (like embedded images or XBRL data)
        text = re.sub(r'\b[a-zA-Z0-9+/=]{100,}\b', '', text)
        
        # Optional: squash multiple lines/spaces into single spaces to further reduce token count
        text = re.sub(r'\s+', ' ', text)
        
        # TRUNCATION FIX: Limit to first ~50,000 characters to prevent hitting rate limits (TPM limits)
        return text.strip()[:50000]
    except Exception as e:
        print(f"Error parsing filing: {e}")
        return ""

SYSTEM_PROMPT = """You are a friendly and clear investment educator. Your goal is to help common people new to investing understand stocks without using heavy financial jargon. 
You provide insights into a company's performance, future relevance, and valuation based on data. 
CRITICAL: You MUST NOT provide explicit 'BUY', 'SELL', or 'HOLD' recommendations. Instead, guide the user with information that helps them make their own decision."""

class LLMService:
    def __init__(self, api_key=None, model_choice='mistral'):
        self.gemini_api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.mistral_api_key = os.getenv("MISTRAL_API_KEY")
        self.model_choice = model_choice

        if self.gemini_api_key:
            genai.configure(api_key=self.gemini_api_key)
        self.gemini_model = genai.GenerativeModel(
            'gemini-flash-latest',
            system_instruction=SYSTEM_PROMPT
        )

        if self.mistral_api_key:
            self.mistral_client = Mistral(api_key=self.mistral_api_key)
        else:
            self.mistral_client = None

    def get_analysis(self, stock_symbol, filings_data, dcf_data, user_query=None, stream=False):
        # Legacy method
        pass

    def get_chat_response(self, history, current_query, stream=False):
        # Legacy method
        pass

    async def get_transcript_highlights(self, transcript_text):
        prompt = f"""
        You are a financial highlights expert.
        Analyze the following earnings call transcript. Specifically find key "Transcript Highlights" that investors should keep in mind.
        These are moments where:
        - The company mentions major financial/strategic shifts (overspending, underspending, etc.).
        - Executives or analysts raise important questions or points about the company's future.
        - Important highlights that someone might otherwise miss in the text.
        
        CRITICAL: Each highlight MUST BE SUBSTANTIALLY LONG. Ensure the clip length (end - start) is at least 20 seconds to provide enough context for the listener. If the transcript portion is shorter, expand the start and end by a few seconds to capture the full context of the point being made.

        The transcript contains timestamps in the format [start_s - end_s].
        Return a JSON array of highlights. Each highlight should be an object with:
        - start: The starting timestamp (number in seconds).
        - end: The ending timestamp (number in seconds).
        - label: A short, catchy title for the highlight (e.g., "Strategic R&D Expansion", "CapEx Context").
        - description: A brief explanation of what is the key takeaway in this clip.

        Example output:
        [
          {{"start": 12.5, "end": 45.2, "label": "R&D Spend Concern", "description": "CEO admitted to 20% increase in R&D with no product timeline."}}
        ]
        
        Return ONLY the raw JSON array. No markdown, no wrappers.
        
        TRANSCRIPT:
        {transcript_text[:100000]}  # Truncate to first 100k chars for safety
        """
        
        if self.model_choice == 'mistral' and self.mistral_client:
            try:
                response = await self.mistral_client.chat.complete_async(
                    model="mistral-large-latest",
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"} if hasattr(self.mistral_client, 'chat') else None
                )
                import json
                text = response.choices[0].message.content.strip()
                # If Mistral returns markdown
                if text.startswith('```json'):
                    text = text[7:-3].strip()
                elif text.startswith('```'):
                    text = text[3:-3].strip()
                return json.loads(text)
            except Exception as e:
                print(f"Mistral Error: {e}")
                return []
        else:
            try:
                import json
                response = await self.gemini_model.generate_content_async(
                    prompt, 
                    generation_config={"response_mime_type": "application/json"}
                )
                return json.loads(response.text)
            except Exception as e:
                print(f"Gemini Error: {e}")
                return []

    async def get_analysis_v2(self, stock_symbol):
        from .models import Stock
        # import DCFService safely
        DCFService_cls = globals().get('DCFService')

        try:
            stock = await sync_to_async(Stock.objects.get)(symbol=stock_symbol)
            # Only analyze the most recent SEC filing
            latest_filing = await sync_to_async(stock.filings.order_by('-accession_number').first)()
            
            def get_all_filings_data():
                return [read_filing_content(latest_filing.content_path)] if latest_filing else []
                
            filings_data = await sync_to_async(get_all_filings_data)()
            dcf_data = await sync_to_async(DCFService_cls.calculate_dcf)(stock_symbol)
        except Exception as e:
            return f"Error fetching data for {stock_symbol}: {str(e)}"

        filings_context = "\n\n".join([f"FILING CONTENT:\n{content}" for content in filings_data]) 
        
        prompt = f"""
        You are a friendly and clear investment educator. Your goal is to help a person who is new to investing understand the stock {stock_symbol} based on the following SEC filings and DCF analysis.
        
        DCF ANALYSIS DATA:
        {dcf_data}
        
        SEC FILINGS DATA (TRUNCATED):
        {filings_context}
        
        Please provide a detailed analysis in plain, simple English that avoids heavy financial jargon. Your objective is not to tell them what to do, but to give them the insights to decide for themselves.
        
        Structure your response with these sections:
        1. **Company Overview & Performance**: How is the company actually doing? Is it growing? Is it healthy? Explain this like you're talking to a friend.
        2. **The Spending Story (Numbers Explained Simply)**: Look at how much they are spending in the background on things like Research & Development (R&D) and building/fixing things (CapEx). Use the actual numbers (e.g., billions or millions) but explain what they mean. Does this spending look like it will actually benefit the company and its investors in the long run, or does it feel like a "black hole"?
        3. **Future Relevance & Direction**: Is the direction the company is taking good for its future? Will it still be relevant in 5-10 years? What are the big things they are working on?
        4. **The 'Fair Value' Insight**: Explain what the DCF analysis says about the stock's value (intrinsic value: {dcf_data.get('intrinsic_value') if isinstance(dcf_data, dict) else 'N/A'}) compared to its current price. Explain what this means for an investor in simple terms.
        5. **Key Risks & Green Flags**: What should an investor watch out for (Risks), and what are the encouraging signs (Green Flags)?
        
        CRITICAL: Use the numbers provided in the DCF DATA (Revenue, Net Income, R&D, etc.) to ground your analysis, but always translate them into "human" terms (e.g., "They spend $X out of every $100 they make on Y"). DO NOT provide a final 'BUY', 'SELL', or 'HOLD' recommendation. Instead, provide a concluding section called 'Investor's Decision Toolkit' where you summarize the most important points.
        
        Output format should be clean Markdown.
        """
        
        if self.model_choice == 'mistral' and self.mistral_client:
            try:
                response = await self.mistral_client.chat.complete_async(
                    model="mistral-medium-latest",
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt}
                    ]
                )
                text = response.choices[0].message.content.strip()
                if text.startswith('```markdown'):
                    text = text[11:]
                elif text.startswith('```'):
                    text = text[3:]
                if text.endswith('```'):
                    text = text[:-3]
                return text.strip()
            except Exception as e:
                return f"Mistral API Error: {str(e)}"
        else:
            try:
                response = await self.gemini_model.generate_content_async(prompt)
                return response.text
            except Exception as e:
                return f"Gemini API Error: {str(e)}"

    async def get_chat_response_v2(self, history, current_query):
        if self.model_choice == 'mistral' and self.mistral_client:
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            for h in history:
                messages.append({"role": h['role'], "content": h['content']})
            messages.append({"role": "user", "content": current_query})
            try:
                response = await self.mistral_client.chat.complete_async(
                    model="mistral-medium-latest",
                    messages=messages
                )
                text = response.choices[0].message.content.strip()
                if text.startswith('```markdown'):
                    text = text[11:]
                elif text.startswith('```'):
                    text = text[3:]
                if text.endswith('```'):
                    text = text[:-3]
                return text.strip()
            except Exception as e:
                return f"Mistral API Error: {str(e)}"
        else:
            gemini_history = []
            for h in history:
                gemini_history.append({"role": "user" if h['role'] == 'user' else "model", "parts": [h['content']]})
            
            try:
                chat = self.gemini_model.start_chat(history=gemini_history)
                response = await chat.send_message_async(current_query)
                return response.text
            except Exception as e:
                return f"Gemini API Error: {str(e)}"

