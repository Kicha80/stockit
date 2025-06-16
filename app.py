from flask import Flask, render_template, jsonify, request, Response, redirect, url_for, flash, send_from_directory
import pandas as pd
import logging
import os
import feedparser
from werkzeug.utils import secure_filename # Make sure this is imported

app = Flask(__name__, static_url_path='/static')
app.secret_key = 'your_very_secret_random_key_here' # IMPORTANT: Change this to a strong, random key
app.config['UPLOAD_FOLDER'] = 'uploads' # Define an upload folder for user uploads
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB max upload size

# Set up logging
logging.basicConfig(level=logging.DEBUG)

# Ensure the upload folder exists
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# Define the folder for dynamic plots (where your scatter plot image and PDF are)
DYNAMIC_PLOTS_FOLDER = os.path.join(app.root_path, 'static', 'dynamic_plots')
if not os.path.exists(DYNAMIC_PLOTS_FOLDER):
    os.makedirs(DYNAMIC_PLOTS_FOLDER)

# Route to serve the dynamic plot image (e.g., current_positions_plot.jpg)
# Although we are removing the image from dashboard.html, keep this route
# in case you want to use it elsewhere or add the plot back later.
@app.route('/dynamic_plot')
def dynamic_plot():
    # This route serves the dynamic image. Ensure your Python script saves it here.
    return send_from_directory(DYNAMIC_PLOTS_FOLDER, 'current_positions_plot.jpg')

# Route to serve files from the 'uploads' directory (e.g., user-uploaded excel, csv, images, PDFs)
# Although we are removing the upload screen from dashboard.html, keep this route
# in case you want to use upload functionality elsewhere.
@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# Helper functions for data loading (from your existing code)
def load_stock_symbols():
    try:
        df = pd.read_excel('stock_names.xlsx')
        return df['Stock'].tolist()
    except Exception as e:
        app.logger.error(f"Error loading stock symbols: {str(e)}")
        return []

def load_industries():
    try:
        df = pd.read_excel('Stocks_top_Perf.xlsx')
        industries = df['Industry'].dropna().unique().tolist()
        return industries
    except Exception as e:
        app.logger.error(f"Error loading industries: {str(e)}")
        return []

def fetch_news_from_rss():
    feed_url = "https://economictimes.indiatimes.com/rssfeedsdefault.cms"  # Example RSS feed URL
    feed = feedparser.parse(feed_url)
    news_items = []
    for entry in feed.entries[:10]:  # Limit to the first 10 news items
        news_items.append({
            'title': entry.title,
            'link': entry.link
        })
    return news_items

# Routes for your web application
@app.route('/')
def index():
    return render_template('index.html')

# UPDATED: Dashboard route to only display the PDF.
@app.route('/dashboard')
def dashboard():
    # No need to list uploaded_files or dynamic plot details as they are no longer displayed on dashboard.html
    return render_template('dashboard.html')

# Although upload screen is removed from dashboard, keep this route if you still use upload functionality elsewhere.
@app.route('/upload_feed', methods=['POST'])
def upload_feed():
    if 'feed_file' not in request.files:
        flash('No file part')
        return redirect(request.url)
    file = request.files['feed_file']
    if file.filename == '':
        flash('No selected file')
        return redirect(request.url)
    if file:
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        flash(f'File "{filename}" uploaded successfully!')
        app.logger.info(f"File uploaded: {filename}")
    return redirect(url_for('dashboard')) # Redirects to the simplified dashboard

@app.route('/get_stock_symbols', methods=['GET'])
def get_stock_symbols():
    symbols = load_stock_symbols()
    return jsonify(symbols)

@app.route('/get_industries', methods=['GET'])
def get_industries():
    industries = load_industries()
    return jsonify(industries)

@app.route('/get_top_performers', methods=['GET'])
def get_top_performers():
    industry = request.args.get('industry')
    if not industry:
        return jsonify({'error': 'Industry not specified'}), 400

    excel_file_path = 'Stocks_top_Perf.xlsx'
    app.logger.info(f"Attempting to read Excel file from path: {os.path.abspath(excel_file_path)}")

    try:
        df = pd.read_excel(excel_file_path)
        industry_data = df[df['Industry'] == industry]
        top_performers = industry_data.nlargest(3, 'Return over 3years')[['Name', 'Return over 3years', 'Market Capitalization']]
        top_performers_dict = top_performers.to_dict(orient='records')
        return jsonify(top_performers_dict)
    except Exception as e:
        app.logger.error(f"Error fetching top performers data: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/get_news_headlines', methods=['GET'])
def get_news_headlines():
    try:
        news_items = fetch_news_from_rss()
        response = jsonify(news_items)
        response.headers['x-content-type-options'] = 'nosniff'
        return response
    except Exception as e:
        app.logger.error(f"Error fetching news headlines: {str(e)}")
        return jsonify({'error': 'Failed to fetch news headlines'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)