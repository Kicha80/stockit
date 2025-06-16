let currentPage = 1;
const rowsPerPage = 10;
let stockDataArray = [];
let chartInstance;

document.getElementById('stock-form').addEventListener('submit', function(event) {
    event.preventDefault();

    document.getElementById('loading-message').style.display = 'block';

    var stockSymbolSelect = document.getElementById('stock-symbol');
    var selectedOptions = Array.from(stockSymbolSelect.selectedOptions).map(option => option.value + '.BSE');
    var fromDate = document.getElementById('from-date').value;
    var toDate = document.getElementById('to-date').value;

    if (new Date(fromDate) > new Date(toDate)) {
        alert('Error: From Date cannot be after To Date.');
        document.getElementById('loading-message').style.display = 'none';
        return;
    }

    Promise.all(selectedOptions.map(stockSymbol => fetchStockData(stockSymbol, fromDate, toDate)))
        .then(data => {
            stockDataArray = data;
            currentPage = 1;
            updateStockDataTable(stockDataArray, fromDate, toDate);
            createStockChart(stockDataArray, fromDate, toDate);
            updatePaginationControls();
            document.getElementById('loading-message').style.display = 'none';
        })
        .catch(error => {
            console.error('Error fetching stock data:', error);
            alert('Error: ' + error.message);
            document.getElementById('loading-message').style.display = 'none';
        });
});

var stockSymbolDropdown = document.getElementById('stock-symbol');
stockSymbolDropdown.innerHTML = '';

fetch('/get_stock_symbols')
    .then(response => response.json())
    .then(data => {
        if (Array.isArray(data)) {
            data.forEach(symbol => {
                var option = document.createElement('option');
                option.value = symbol;
                option.text = symbol.split('.')[0];
                stockSymbolDropdown.appendChild(option);
            });
        } else {
            throw new Error('Invalid data format received from server.');
        }
    })
    .catch(error => {
        console.error('Error fetching stock symbols:', error);
        alert('Error fetching stock symbols. Please try again.');
    });

document.getElementById('industry-dropdown').addEventListener('change', function() {
    var selectedIndustry = this.value;

    fetch('/get_top_performers?industry=' + encodeURIComponent(selectedIndustry))
        .then(response => {
            if (!response.ok) {
                throw new Error('Network response was not ok');
            }
            return response.json();
        })
        .then(data => {
            updateTopPerformersChart(data);
        })
        .catch(error => {
            console.error('Error fetching top performers data:', error);
            alert('Error fetching top performers data. Please try again.');
        });
});

// Function to start scrolling the news feed
function startScrollingNewsFeed() {
    const newsFeed = document.getElementById('news-feed');
    const originalContent = newsFeed.innerHTML;
    newsFeed.innerHTML += originalContent; // Duplicate the content

    let scrollPosition = 0;
    const totalHeight = newsFeed.scrollHeight / 2; // Use half of the duplicated content's height

    function scrollNews() {
        scrollPosition++;
        newsFeed.style.transform = `translateY(-${scrollPosition}px)`;
        if (scrollPosition >= totalHeight) { // Reset when half the content has scrolled
            scrollPosition = 0;
        }
        requestAnimationFrame(scrollNews);
    }

    requestAnimationFrame(scrollNews);
}

// Start scrolling the news feed when the document is ready
document.addEventListener('DOMContentLoaded', function() {
    startScrollingNewsFeed();
});

function updateTopPerformersChart(data) {
    var topPerformersChart = document.getElementById('top-performers-chart').getContext('2d');
    if (window.topPerformersChartInstance) {
        window.topPerformersChartInstance.destroy();
    }

    var labels = data.map(stock => stock.Name).slice(0, 3);
    var returns = data.map(stock => stock['Return over 3years']).slice(0, 3);
    var marketCaps = data.map(stock => stock['Market Capitalization']).slice(0, 3);

    window.topPerformersChartInstance = new Chart(topPerformersChart, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: 'Return over 3years',
                data: returns,
                backgroundColor: 'rgba(255, 99, 132, 0.6)',
                borderWidth: 1
            }, {
                label: 'Market Capitalization',
                data: marketCaps,
                backgroundColor: 'rgba(54, 162, 235, 0.6)',
                borderWidth: 1
            }]
        },
        options: {
            scales: {
                y: {
                    beginAtZero: true
                }
            }
        }
    });
}

fetch('/get_industries')
    .then(response => response.json())
    .then(data => {
        const industryDropdown = document.getElementById('industry-dropdown');
        industryDropdown.innerHTML = '';

        if (Array.isArray(data)) {
            data.forEach(industry => {
                var option = document.createElement('option');
                option.value = industry;
                option.text = industry;
                industryDropdown.appendChild(option);
            });
        } else {
            throw new Error('Invalid data format received from server.');
        }
    })
    .catch(error => {
        console.error('Error fetching industries:', error);
        alert('Error fetching industries. Please try again.');
    });

fetchNewsHeadlines();

function fetchStockData(stockSymbol, fromDate, toDate) {
    var apiKey = 'IL8BY1S1UKLFM8AO';
    var apiUrl = 'https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol=' + stockSymbol + '&apikey=' + apiKey + '&outputsize=full';

    return fetch(apiUrl)
        .then(function(response) {
            if (!response.ok) {
                throw new Error('HTTP error, status = ' + response.status);
            }
            return response.json();
        })
        .then(function(data) {
            if (data['Error Message']) {
                throw new Error('Stock symbol ' + stockSymbol + ' data is not available at this moment. Apologies.');
            }
            if (!data['Time Series (Daily)']) {
                throw new Error('No data found for the given stock symbol.');
            }
            return { stockSymbol, timeSeries: data['Time Series (Daily)'] };
        })
        .catch(function(error) {
            console.error('Error fetching stock data:', error);
            alert(error.message); 
            resetPage(); 
        });
}

function resetPage() {
    document.getElementById('stock-symbol').selectedIndex = -1;
    document.getElementById('from-date').value = '';
    document.getElementById('to-date').value = '';

    var tableBody = document.querySelector('#stock-data tbody');
    tableBody.innerHTML = '';

    document.getElementById('page-info').textContent = 'Page 1';
    document.getElementById('prev-page').disabled = true;
    document.getElementById('next-page').disabled = true;

    var ctx = document.getElementById('stock-chart').getContext('2d');
    if (chartInstance) {
        chartInstance.destroy();
    }
}

function formatDecimal(number) {
    return Number(number).toFixed(2); 
}

function updateStockDataTable(stockDataArray, fromDate, toDate) {
    var tableBody = document.getElementById('stock-data').getElementsByTagName('tbody')[0];
    tableBody.innerHTML = '';

    var fromDateObj = new Date(fromDate);
    var toDateObj = new Date(toDate);
    var rowsAdded = 0;

    stockDataArray.forEach(stockData => {
        var stockSymbol = stockData.stockSymbol.split('.')[0];
        var timeSeries = stockData.timeSeries;

        for (var date in timeSeries) {
            var dateObj = new Date(date);
            if (dateObj >= fromDateObj && dateObj <= toDateObj) {
                if (rowsAdded >= (currentPage - 1) * rowsPerPage && rowsAdded < currentPage * rowsPerPage) {
                    var row = tableBody.insertRow();
                    row.insertCell(0).textContent = stockSymbol;
                    row.insertCell(1).textContent = date;
                    row.insertCell(2).textContent = formatDecimal(timeSeries[date]['1. open']);
                    row.insertCell(3).textContent = formatDecimal(timeSeries[date]['2. high']);
                    row.insertCell(4).textContent = formatDecimal(timeSeries[date]['3. low']);
                    row.insertCell(5).textContent = formatDecimal(timeSeries[date]['4. close']);
                    row.insertCell(6).textContent = formatDecimal(timeSeries[date]['5. volume']);
                }
                rowsAdded++;
            }
        }
    });

    updatePaginationControls();
}

function createStockChart(stockDataArray, fromDate, toDate) {
    var fromDateObj = new Date(fromDate);
    var toDateObj = new Date(toDate);

    var dates = [];
    var isDatesPopulated = false;

    var datasets = stockDataArray.map((stockData, index) => {
        var { stockSymbol, timeSeries } = stockData;
        var closeValues = [];

        for (var date in timeSeries) {
            var dateObj = new Date(date);
            if (dateObj >= fromDateObj && dateObj <= toDateObj) {
                if (!isDatesPopulated) {
                    dates.push(date);
                }
                closeValues.push(timeSeries[date]['4. close']);
            }
        }

        if (!isDatesPopulated) {
            dates.reverse();
            isDatesPopulated = true;
            closeValues.reverse();
        }
        return {
            label: stockSymbol.split('.')[0],
            data: closeValues,
            borderColor: getBorderColor(index),
            backgroundColor: 'rgba(75, 192, 192, 0.2)',
            fill: false,
        };
    });

    var ctx = document.getElementById('stock-chart').getContext('2d');
    if (chartInstance) {
        chartInstance.destroy();
    }

    chartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: dates,
            datasets: datasets
        },
        options: {
            responsive: true,
            title: {
                display: true,
                text: 'Stock Close Price Over Time'
            },
            scales: {
                x: {
                    type: 'time',
                    time: {
                        unit: 'day',
                        displayFormats: {
                            day: 'dd/MM'
                        }
                    },
                    title: {
                        display: true,
                        text: 'Date'
                    }
                },
                y: {
                    title: {
                        display: true,
                        text: 'Close Price'
                    }
                }
            }
        }
    });
}

function getBorderColor(index) {
    const colors = ['red', 'blue', 'green', 'orange', 'purple'];
    return colors[index % colors.length];
}

function updatePaginationControls() {
    document.getElementById('page-info').textContent = 'Page ' + currentPage;
    document.getElementById('prev-page').disabled = currentPage === 1;
    document.getElementById('next-page').disabled = stockDataArray[0].timeSeries ? Object.keys(stockDataArray[0].timeSeries).length <= currentPage * rowsPerPage : true;
}

function prevPage() {
    if (currentPage > 1) {
        currentPage--;
        updateStockDataTable(stockDataArray, document.getElementById('from-date').value, document.getElementById('to-date').value);
        updatePaginationControls();
    }
}

function nextPage() {
    var totalRows = stockDataArray.reduce(function(total, stockData) {
        var timeSeries = stockData.timeSeries;
        var fromDateObj = new Date(document.getElementById('from-date').value);
        var toDateObj = new Date(document.getElementById('to-date').value);
        var rows = 0;
        for (var date in timeSeries) {
            var dateObj = new Date(date);
            if (dateObj >= fromDateObj && dateObj <= toDateObj) {
                rows++;
            }
        }
        return total + rows;
    }, 0);

    if (currentPage < Math.ceil(totalRows / rowsPerPage)) {
        currentPage++;
        updateStockDataTable(stockDataArray, document.getElementById('from-date').value, document.getElementById('to-date').value);
        updatePaginationControls();
    }
}

function fetchNewsHeadlines() {
    fetch('/get_news_headlines')
        .then(response => response.json())
        .then(data => {
            updateNewsFeed(data);
        })
        .catch(error => {
            console.error('Error fetching news headlines:', error);
            alert('Error fetching news headlines. Please try again.');
        });
}

function updateNewsFeed(articles) {
    const newsFeed = document.getElementById('news-feed');
    newsFeed.innerHTML = '';

    if (articles.length > 0) {
        articles.forEach(article => {
            const li = document.createElement('li');
            const a = document.createElement('a');
            a.href = article.link;
            a.textContent = article.title;
            li.appendChild(a);
            newsFeed.appendChild(li);
        });
    } else {
        newsFeed.innerHTML = '<li>No news available</li>';
    }
}

// Initialize Select2 for the stock-symbol dropdown
$(document).ready(function() {
    $('#stock-symbol').select2({
        placeholder: "Select Stock Symbols",
        width: '100%'
    });
});

function updateTime() {
    const now = new Date();
    const hours = now.getUTCHours().toString().padStart(2, '0');
    const minutes = now.getUTCMinutes().toString().padStart(2, '0');
    const seconds = now.getUTCSeconds().toString().padStart(2, '0');
    document.getElementById('time').textContent = `Time: ${hours}:${minutes}:${seconds} GMT`;
}

// Update time every second
setInterval(updateTime, 1000);

// Initialize the time immediately
updateTime();
