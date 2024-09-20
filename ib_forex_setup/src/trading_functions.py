# Import the necessary libraries
import pytz
import numpy as np
import pandas as pd
import datetime as dt
from datetime import datetime
from lightgbm import LGBMClassifier
from shaphypetune import BoostBoruta

def dropLabels(events,minPct=.05):
    ''' Function to drop the lowest-percentage prediction feature class'''
    # apply weights, drop labels with insufficient examples
    while True:
        # Count the total number of observations per each prediction feature class
        df0=events['y'].value_counts(normalize=True)
        # If the class with the minimum number of observations or if there is only 2 prediction feature labels, then finish the loop
        if (df0.min()>minPct) or (df0.shape[0]<3):break
        # Drop the prediction feature label which has the lowest number of observations
        events = events[events['y']!=df0.index[df0.argmin()]]
    return events

def get_mid_series(dfraw):
    ''' Function to get the midpoint series'''
    # Copy the dataframe 
    dfraw = dfraw.copy(deep=True)
    # Set the OHLC names list
    prices = ['Close','High','Low','Open',]
    # Forward-fill the dataframe just in case
    dfraw.ffill(inplace=True)
    # Create a new dataframe with the previous dataframe index
    df = pd.DataFrame(index=dfraw.index)
    # Looping against the prices' names
    for price in prices:
        # Get the midpoint of each price
        df[f'{price}'] = ( dfraw[f'bid_{price.lower()}'] + dfraw[f'ask_{price.lower()}'] ) /2        
    # Sort the dataframe by index
    df.sort_index(ascending=True, inplace=True)
    # Drop NaN values just in case
    df.dropna(inplace=True)
    return df

def resample_df(dfraw,frequency,start='00h00min'):
    ''' Function to resample the data'''
    # Copy the dataframe
    df = dfraw.copy()   
    # Get the start hour
    hour=int(start[0:2])
    # Get the start minute time    
    minutes=int(start[3:5])
    
    # Set the first day of the new dataframe
    origin = df[(df.index.hour==hour) & (df.index.minute==minutes)].index[0]
    # Subset the dataframe from the origin onwards
    df = df[df.index>=origin]
    # Create a datetime column based on the index
    df['datetime'] = df.index
    # Create a new dataframe
    df2 = (df.groupby(pd.Grouper(freq=frequency, origin=df.index[0]))
           # Resample the Open price
            .agg(Open=('Open','first'),
                 # Resample the Close price
                 Close=('Close','last'),
                 # Resample the High price
                 High=('High','max'),
                 # Resample the Low price
                 Low=('Low','min'),
                 # Get the High-price index
                 High_time=('High', lambda x : np.nan if x.count() == 0 else x.idxmax()),
                 # Get the Low-price index
                 Low_time=('Low', lambda x : np.nan if x.count() == 0 else x.idxmin()),
                 # Get the Open-price index
                 Open_time=('datetime','first'),
                 # Get the Close-price index
                 Close_time=('datetime','last'))
            # Create a column and set each row to True in case the high price index is sooner than the low price index
            .assign(high_first = lambda x: x["High_time"] < x["Low_time"])
            )
    
    final_df = df2.shift(1)
        
    if 'h' in frequency:
        final_df.loc[df2.index[-1]+dt.timedelta(hours=int(frequency[:frequency.find("h")])),:] = df2.loc[df2.index[-1],:]
    else:
        final_df.loc[df2.index[-1]+dt.timedelta(minutes=int(frequency[:frequency.find("min")])),:] = df2.loc[df2.index[-1],:]
        
    final_df.dropna(inplace=True)
        
    return final_df

def directional_change_events(data, theta=0.004, columns=None):
    """ Function to create the DC indicators provided by Chen and Tsang (2021) """

    # Copy the dataframe
    data = data.copy()

    # Create the necessary columns and variables
    data["Event"] = 0.0

    # Set the initial event variable value
    event = "upward" # initial event

    # Set the initial value for low and high prices
    ph = data['Close'].iloc[0] # highest price
    pl = data['Close'].iloc[0] # lowest price

    # Create loop to run through each date
    for t in range(0, len(data.index)):
        # Check if we're on a downward trend
        if event == "downward":
            # Check if the close price is higher than the low price by the theta threshold
            if data["Close"].iloc[t] >= pl * (1 + theta):
                # Set the event variable to upward
                event = "upward"
                # Set the high price as the current close price                
                ph = data["Close"].iloc[t]
            # If the close price is lower than the low price by the theta threshold
            else:
                # Check if the close price is less than the low price
                if data["Close"].iloc[t] < pl:
                    # Set the low price as the current close price
                    pl = data["Close"].iloc[t]
                    # Set the Event to upward for the current period
                    data["Event"].iloc[t] = 1
        # Check if we're on an upward trend
        elif event == "upward":
            # Check if the close price is less than the high price by the theta threshold
            if data["Close"].iloc[t] <= ph * (1 - theta):  
                # Set the event variable to downward
                event = "downward"
                # Set the low price as the current close price
                pl = data["Close"].iloc[t]
            # If the close price is higher than the high price by the theta threshold
            else:
                # Check if the close price is higher than the high price
                if data["Close"].iloc[t] > ph:
                    # Set the high price as the current close price
                    ph = data["Close"].iloc[t]
                    # Set the Event to downward for the current period
                    data["Event"].iloc[t] = -1

    # Set the peak and trough prices and forward-fill the column
    data['peak_trough_prices'] = np.where(data['Event']!=0, data['Close'],0)
    data['peak_trough_prices'].replace(to_replace=0, method='ffill', inplace=True)

    # Count the number of periods between a peak and a trough
    data['count'] = 0
    for i in range(1,len(data.index)):
        if data['Event'].iloc[(i-1)]!=0:
            data['count'].iloc[i] = 1+data['count'].iloc[(i-1)]
        else:
            data['count'].iloc[i] = 1

    # Compute the TMV indicator
    data['TMV'] = np.where(data['Event']!=0, abs(data['peak_trough_prices']-data['peak_trough_prices'].shift())/\
                          (data['peak_trough_prices'].shift()*theta),0)

    # Compute the time-completion-for-a-trend indicator
    data['T'] = np.where(data['Event']!=0, data['count'],0)

    # Compute the time-adjusted-return indicator and forward-fill it
    data['R'] = np.where(data['Event']!=0, np.log(data['TMV']/data['T']*theta),0)
    data['R'] = data['R'].replace(to_replace=0, method='ffill')

    # Drop NaN or infinite values
    data.replace([np.inf, -np.inf], np.nan, inplace=True)
    
    if columns is None:
        return data
    else:
        return data[columns]

def library_boruta_shap(X, y, seed, max_iter, date_loc):
    """ Function to compute the Boruta-Shap algorithm and get the best features"""    
    X_train, X_test = X.loc[:date_loc,:], X.loc[date_loc:,:]
    y_train, y_test = y.loc[:date_loc, 'y'].values.reshape(-1,), y.loc[date_loc:, 'y'].values.reshape(-1,)
    
    # Parameters' range of values
    param_grid = {
                    'learning_rate': [0.2, 0.1],
                    'num_leaves': [25],#, 35],
                    'max_depth': [12]
                }

    clf_lgbm = LGBMClassifier(n_estimators=20, random_state=seed, n_jobs=-2)
    
    ### HYPERPARAM TUNING WITH GRID-SEARCH + BORUTA SHAP ###
    try:
        model = BoostBoruta(clf_lgbm, param_grid=param_grid, max_iter=max_iter, perc=100,
                            importance_type='shap_importances', train_importance=False, sampling_seed=seed, n_jobs=-2, early_stopping_boruta_rounds=6, verbose=0)
        
        model.fit(X_train, y_train, eval_set=[(X_test, y_test)])
        
        best_features = model.support_.tolist()
        
        if len(best_features)!=0:
            return best_features
        else:
            return X.columns.tolist()
    except:
        best_features = X.columns.tolist()
        return best_features

def create_Xy(indf, feature_cols, y_target_col): 
    """ Function to create the input and prediction features dataframes """
    # Create the input features and prediction features dataframes
    X, y = indf[feature_cols], indf[y_target_col].to_frame()
    return X, y

def roll_zscore(x, window):
    """ Function to create the rolling zscore versions of an array """
    # Create the rolling object
    r = x.rolling(window=window)
    # Set the rolling mean
    m = r.mean().shift(1)
    # Set the rolling standard deviation
    s = r.std().shift(1)
    # Compute the zscore values
    z = (x - m) / s
    return z

def rolling_zscore_function(data, scalable_features, window):
    """ Function to create the rolling zscore versions of the feature inputs """
    # Create a scaled X dataframe based on the data index
    X_scaled_final = pd.DataFrame(index=data.index)
    # Create the scaled X data 
    X_scaled = roll_zscore(data[scalable_features], window=window)
    # Replace the infinite values with NaN values
    X_scaled.replace([np.inf, -np.inf], np.nan, inplace=True)
        
    # Create a scaled features list
    scaled_features = list()
    
    # Loop through the scaled X data columns
    for feature in X_scaled.columns:
        # If the number of NaN values is higher than the max window used to compute the technical indicators
        if np.isnan(X_scaled[feature]).sum()>window:
            # Save in the scaled X data the same non-scaled feature column
            X_scaled_final[feature] = data[feature].copy()
        # If the number of NaN values is lower than the max window used to compute the technical indicators
        else:
            # Save the scaled column in the final dataframe
            X_scaled_final[feature] = X_scaled[feature].copy()
            # Save the scaled X column name in the list
            scaled_features.append(feature)
            
    # Concatenate the rest of the columns not used in the X_scaled dataframe
    X_scaled_final = pd.concat([X_scaled_final, data[data.columns.difference(scalable_features)]], axis=1)
            
    # Drop the NaN values
    X_scaled_final.dropna(inplace=True)
    
    return X_scaled_final, scaled_features

def train_test_split(X, y, split, purged_window_size, embargo_period):
    """ Function to split the data into train and test data """
    # If the split variable is an integer
    if isinstance(split, int):
        # Get the train data
        X_train, y_train = X.iloc[:-split], y.iloc[:-split]
        # Get the test data
        X_test, y_test = pd.DataFrame(X.iloc[-split:], index=X.index[-split:]), pd.DataFrame(y.iloc[-split:], index=y.index[-split:])
        
    # The purged start iloc value
    purged_start = max(0, len(y) - (len(y) - purged_window_size))
    # The embargo start iloc value
    embargo_start = max(0, len(y) - embargo_period)
       
    # Defining once again the X train data based on the purged window and embargo period
    X_train = X_train.iloc[purged_start:embargo_start]
    # Defining once again the y train data based on the purged window and embargo period
    y_train = y_train.iloc[purged_start:embargo_start]
    
    return X_train, X_test, y_train, y_test

def define_trading_week(local_timezone, trading_start_hour, day_end_minute):
    """ Function to get the current trading week start and end datetimes """
        
    # Set the now datetime
    today = dt.datetime.now().astimezone(pytz.timezone(local_timezone))
    
    # Set the easter timezone string
    bog = 'America/Bogota'
    # Set the local-timezone-based today's datetime
    bogota_datetime = today.astimezone(pytz.timezone(bog)).replace(tzinfo=None)
    
    # Bogota-based start datetime
    bogota_trading_start_datetime = today.replace(hour=trading_start_hour, minute=day_end_minute, second=0, microsecond=0).astimezone(pytz.timezone(bog)).replace(tzinfo=None)
    # Bogota-based start hour
    bogota_trading_start_hour = bogota_trading_start_datetime.hour
    # Bogota-based start minute
    bogota_trading_start_minute = bogota_trading_start_datetime.minute
    
    # If we're out of trading hours (This is for Forex trading hours)
    if (bogota_datetime.weekday()==4 and bogota_datetime.hour>=bogota_trading_start_hour and bogota_datetime.minute>=bogota_trading_start_minute) or \
        (bogota_datetime.weekday()==5) or \
        (bogota_datetime.weekday()==6 and bogota_datetime.hour<=bogota_trading_start_hour and bogota_datetime.minute<=bogota_trading_start_minute):
        # Set the Sunday datetime
        sunday = (bogota_datetime + dt.timedelta( (6-bogota_datetime.weekday()) % 7 ))
        # Set the Friday datetime
        friday = (bogota_datetime + dt.timedelta( (4-bogota_datetime.weekday()) % 7 ))
    # If we're in the trading hours (This is for Forex trading hours)
    else:
        # Set the Friday datetime
        friday = (bogota_datetime + dt.timedelta( (4-bogota_datetime.weekday()) % 7 ))
        # Set the Sunday datetime
        sunday = (bogota_datetime - dt.timedelta( (bogota_datetime.weekday()-6) % 7 ))  
    
    # Set the trading week start datetime
    week_start = dt.datetime(sunday.year,sunday.month,sunday.day,bogota_trading_start_hour,bogota_trading_start_minute,0)
    # Set the trading week end datetime
    week_end = dt.datetime(friday.year,friday.month,friday.day,bogota_trading_start_hour,bogota_trading_start_minute,0)
    
    # Localize the week start datetime to Bogota's timezone
    week_start = pytz.timezone(bog).localize(week_start)
    # Localize the week end datetime to Bogota's timezone
    week_end = pytz.timezone(bog).localize(week_end)
    
    # Convert the week start datetime to the trader's timezone 
    week_start = week_start.astimezone(pytz.timezone(local_timezone)).replace(tzinfo=None)
    # Convert the week end datetime to the trader's timezone 
    week_end = week_end.astimezone(pytz.timezone(local_timezone)).replace(tzinfo=None)
    
    return week_start, week_end 

def save_xlsx(dict_df, path):
    """
    Function to save a dictionary of dataframes to an Excel file, with each dataframe as a separate sheet
    """
    writer = pd.ExcelWriter(path)
    for key in dict_df:
        dict_df[key].to_excel(writer, key)
    writer.close()

def get_end_hours(timezone, london_start_hour, local_restart_hour):
    """ Function to get the end hours based on the Eastern timezone """
    
    # Set the easter timezone string
    est = 'US/Eastern'
    # Get today's datetime
    today_datetime = dt.datetime.now()
    # Set the eastern-timezone-based today's datetime
    eastern = today_datetime.astimezone(pytz.timezone(est))
    # Get the timezone difference hour and minute
    eastern_timestamp = eastern.strftime("%z")
    # Get the eastern timezone difference sign boolean
    eastern_negative_sign_bool = eastern_timestamp.startswith("-")
    # Set the eastern timezone difference sign number
    eastern_sign = -1 if eastern_negative_sign_bool else +1
    
    # Set the trader's timezone now datetime
    trader_datetime = today_datetime.astimezone(pytz.timezone(timezone))
    # Get the timezone difference hour and minute
    trader_datetime_timestamp = trader_datetime.strftime("%z")
    # Get the trader's timezone difference sign boolean
    trader_datetime_negative_sign_bool = trader_datetime_timestamp.startswith("-")
    # Set the trader's timezone difference sign number
    trader_datetime_sign = -1 if trader_datetime_negative_sign_bool else +1
    # Get the number of minutes of the difference between both datetimes
    minutes = int(str(abs(trader_datetime.replace(tzinfo=None) - eastern.replace(tzinfo=None)))[2:4])
    
    # If the trader's timezone sign is different from Eastern's
    if trader_datetime_sign != eastern_sign:
        # Set the restart hour
        restart_hour = local_restart_hour + int(eastern_timestamp[1:3])+int(trader_datetime_timestamp[1:3])
        restart_hour = restart_hour if restart_hour<=23 else restart_hour - 24
        
        # Set the day-end hour
        day_end_hour = 17 + int(eastern_timestamp[1:3])+int(trader_datetime_timestamp[1:3])
        day_end_hour = day_end_hour if day_end_hour<=23 else day_end_hour - 24
        
        # Set the restart minute
        restart_minute = day_end_minute = minutes
    # If the trader's timezone sign is equal to Eastern's
    else:
        # Set the restart hour
        restart_hour = local_restart_hour + int(eastern_timestamp[1:3])-int(trader_datetime_timestamp[1:3])
        restart_hour = restart_hour if restart_hour<=23 else restart_hour - 24
        
        # Set the day-end hour
        day_end_hour = 17 + int(eastern_timestamp[1:3])-int(trader_datetime_timestamp[1:3])
        day_end_hour = day_end_hour if day_end_hour<=23 else day_end_hour - 24
        
        # Set the restart minute
        restart_minute = day_end_minute = minutes
      
    # Set the trading start hour
    trading_start_hour = london_start_hour + trader_datetime_sign*int(trader_datetime_timestamp[1:3])
    trading_start_hour = trading_start_hour if trading_start_hour<=23 else (trading_start_hour - 24)
                    
    return restart_hour, restart_minute, day_end_hour, day_end_minute, trading_start_hour
        
def get_data_frequency_values(data_frequency):
    """ Function to get the data frequency number and string """
    
    # If the data frequency is in minutes
    if 'min' in data_frequency:
        # Set the frequency number
        frequency_number = int(data_frequency[:data_frequency.find("min")])
        # Set the frequency string
        frequency_string = data_frequency[data_frequency.find("min"):]
    # If the data frequency is in hours
    elif 'h' in data_frequency:
        # Set the frequency number
        frequency_number = int(data_frequency[:data_frequency.find("h")])
        # Set the frequency string
        frequency_string = data_frequency[data_frequency.find("h"):]
        
    return frequency_number, frequency_string

def get_periods_per_day(data_frequency):
    """ Function to get the number of periods per day as per the data frequency """
    
    # Get the data frequency number and string
    frequency_number, frequency_string = get_data_frequency_values(data_frequency)
    
    # If the data frequency is in minutes
    if frequency_string == 'min':
        # Return the periods per day
        return 24*(60//frequency_number)
    # If the data frequency is in hours
    elif frequency_string == 'h':
        # Return the periods per day
        return 24//frequency_number

def get_restart_and_day_close_datetimes(data_frequency, now_datetime, day_end_hour, day_end_minute, restart_hour, restart_minute, trading_start_hour):
    """ Function to get the restart and day close datetimes """
    
    # If the now hour is sooner than the day-end hour
    if now_datetime.hour <= day_end_hour:
        # Set the start datetime
        start_datetime = (now_datetime - dt.timedelta(days=1)).replace(hour=trading_start_hour, minute=day_end_minute,second=0, microsecond=0)
    # If the now hour is later than the day-end hour
    else:
        # Set the start datetime
        start_datetime = now_datetime.replace(hour=trading_start_hour, minute=day_end_minute, second=0, microsecond=0)
        
    # If the now hour is later than the restart hour
    if now_datetime.hour >= restart_hour:
        # Set the restart end datetime
        auto_restart_end_datetime = (now_datetime + dt.timedelta(days=1)).replace(hour=restart_hour, minute=restart_minute,second=0, microsecond=0)
    # If the now hour is sooner than the restart hour
    else:
        # Set the restart end datetime
        auto_restart_end_datetime = now_datetime.replace(hour=restart_hour, minute=restart_minute,second=0, microsecond=0)
    
    # Set the day-end datetime
    day_end_datetime = day_datetime_before_end = (start_datetime + dt.timedelta(days=1)).replace(hour=day_end_hour)         
    # Set the day-end datetime in which we're going to close all positions
    trading_day_end_datetime = day_datetime_before_end = (start_datetime + dt.timedelta(days=1)).replace(hour=day_end_hour)   
    # Set the previous day start datetime
    previous_day_start_datetime = (trading_day_end_datetime - dt.timedelta(days=1)).replace(hour=trading_start_hour, minute=day_end_minute, microsecond=0)
    # Set the auto-restart start datetime
    auto_restart_start_datetime = auto_restart_datetime_before_end = auto_restart_end_datetime 

    # If the data frequency is in minutes
    if 'min' in data_frequency:
        # Set the frequency number
        frequency_number = int(data_frequency[:data_frequency.find("min")])
                
        # Create a frequency periods' list with the start datetime as the initial value
        frequency_periods = [start_datetime]
        
        # If the trading day-end datetime is later than the auto-restart end datetime
        if trading_day_end_datetime > auto_restart_end_datetime:

            # Fill the frequency_periods list up to the trading day-end datetime
            i = 0
            while frequency_periods[i] <= trading_day_end_datetime:
                frequency_periods.append(frequency_periods[i] + dt.timedelta(minutes=frequency_number))
                i += 1

            # Set the last trading datetime before the IB platform is auto-restarted
            for i in range(len(frequency_periods)):
                if frequency_periods[i] > auto_restart_end_datetime:
                    auto_restart_datetime_before_end = frequency_periods[i-1]
                    break
                
            # Loop to get the auto-restart start datetime
            for i in range(len(frequency_periods)):
                if frequency_periods[i] >= auto_restart_end_datetime:
                    if (frequency_periods[i] >= auto_restart_end_datetime.replace(minute=5)):
                        auto_restart_start_datetime = frequency_periods[i] 
                        break
    
            # Set last day datetime before the day is closed
            for i in range((len(frequency_periods)-1),0,-1):
                if (trading_day_end_datetime-frequency_periods[i]) > dt.timedelta(minutes=30):
                    day_datetime_before_end = frequency_periods[i]
                    break
            
        # If the trading day-end datetime is sooner than the auto-restart end datetime
        else:
            
            # Fill the frequency_periods list up to the trading day-end datetime
            i = 0
            while frequency_periods[i] <= trading_day_end_datetime:
                frequency_periods.append(frequency_periods[i] + dt.timedelta(minutes=frequency_number))
                i += 1

            # Set last day datetime before the day is closed
            for i in range((len(frequency_periods)-1),0,-1):
                if (trading_day_end_datetime-frequency_periods[i]) > dt.timedelta(minutes=30):
                    day_datetime_before_end = frequency_periods[i]
                    break

            # Create a second frequency periods' list with the start datetime as the initial value
            frequency_periods2 = [trading_day_end_datetime.replace(hour=day_end_hour+1)]
            i = 0
            # Fill the second frequency_periods list up to the auto-restart end datetime
            while frequency_periods2[i] >= auto_restart_end_datetime:
                frequency_periods2.append(frequency_periods2[i] + dt.timedelta(minutes=frequency_number))
                i += 1
            
            # Set the last trading datetime before the IB platform is auto-restarted
            for i in range(len(frequency_periods2)):
                if frequency_periods2[i] > auto_restart_end_datetime:                    
                    auto_restart_datetime_before_end = frequency_periods2[i-1]
                    break
                
            # Loop to get the auto-restart start datetime
            i = len(frequency_periods2)-1
            while auto_restart_start_datetime < auto_restart_end_datetime.replace(minute=5):
                if auto_restart_start_datetime >= auto_restart_end_datetime.replace(minute=5):
                    break
                auto_restart_start_datetime = frequency_periods2[i] + dt.timedelta(minutes=frequency_number)
                frequency_periods2.append(auto_restart_start_datetime)
                i += 1
                
    elif 'h' in data_frequency:
        # Set the frequency number
        frequency_number = int(data_frequency[:data_frequency.find("h")])
                
        # Create a frequency periods' list with the start datetime as the initial value
        frequency_periods = [start_datetime]
        
        # If the trading day-end datetime is later than the auto-restart end datetime
        if trading_day_end_datetime > auto_restart_end_datetime:

            # Fill the frequency_periods list up to the trading day-end datetime
            i = 0
            while frequency_periods[i] <= trading_day_end_datetime:
                frequency_periods.append(frequency_periods[i] + dt.timedelta(hours=frequency_number))
                i += 1

            # Set the last trading datetime before the IB platform is auto-restarted
            for i in range(len(frequency_periods)):
                if frequency_periods[i] > auto_restart_end_datetime:
                    auto_restart_datetime_before_end = frequency_periods[i-1]
                    break
                
            # Loop to get the auto-restart start datetime
            for i in range(len(frequency_periods)):
                if frequency_periods[i] >= auto_restart_end_datetime:
                    if (frequency_periods[i] >= auto_restart_end_datetime.replace(minute=5)):
                        auto_restart_start_datetime = frequency_periods[i]  
                        break
    
            # Set last day datetime before the day is closed
            for i in range((len(frequency_periods)-1),0,-1):
                if (trading_day_end_datetime-frequency_periods[i]) > dt.timedelta(minutes=30):
                    day_datetime_before_end = frequency_periods[i]
                    break

        # If the trading day-end datetime is sooner than the auto-restart end datetime
        else:
            
            # Fill the frequency_periods list up to the trading day-end datetime
            i = 0
            while frequency_periods[i] <= trading_day_end_datetime:
                frequency_periods.append(frequency_periods[i] + dt.timedelta(hours=frequency_number))
                i += 1

            # Set last day datetime before the day is closed
            for i in range((len(frequency_periods)-1),0,-1):
                if (trading_day_end_datetime-frequency_periods[i]) > dt.timedelta(minutes=30):
                    day_datetime_before_end = frequency_periods[i]
                    break

            # Create a second frequency periods' list with the start datetime as the initial value
            frequency_periods2 = [trading_day_end_datetime.replace(hour=day_end_hour+1)]
            # Fill the second frequency_periods list up to the auto restart end datetime
            i = 0
            while frequency_periods2[i] >= auto_restart_end_datetime:
                frequency_periods2.append(frequency_periods2[i] + dt.timedelta(minutes=frequency_number))
                i += 1

            # Set the last trading datetime before the IB platform is auto-restarted
            for i in range(len(frequency_periods2)):
                if frequency_periods2[i] > auto_restart_end_datetime:                    
                    auto_restart_datetime_before_end = frequency_periods2[i-1]
                    break
                
            # Loop to get the auto-restart start datetime
            i = len(frequency_periods2)-1
            while True:
                if auto_restart_start_datetime >= auto_restart_end_datetime.replace(minute=5):
                    break
                auto_restart_start_datetime = frequency_periods2[i] + dt.timedelta(hours=frequency_number)
                frequency_periods2.append(auto_restart_start_datetime)
                i += 1
            
    # Get the actual trading day-end datetime
    trading_day_end_datetime = trading_day_end_datetime.replace(hour=day_end_hour-1,minute=30,second=0)   

    # Set the day start datetime 
    day_start_datetime = previous_day_start_datetime + dt.timedelta(days=1)
    
    return auto_restart_start_datetime, auto_restart_datetime_before_end, auto_restart_end_datetime, \
            day_start_datetime, \
            day_datetime_before_end,  \
            trading_day_end_datetime, \
            day_end_datetime, previous_day_start_datetime

def get_frequency_change(data_frequency):
    """ Function to get data frequency timedelta """
    
    # If data frequency is in minutes
    if 'min' in data_frequency:
        # Define the data frequency timedelta
        time_change = dt.timedelta(minutes=int(data_frequency[:data_frequency.find("min")]))
    # If data frequency is in hours
    elif 'hour' in data_frequency:
        # Define the data frequency timedelta
        time_change = dt.timedelta(hour=int(data_frequency[:data_frequency.find("h")])) 

    return time_change

def get_todays_periods(now_, data_frequency, previous_day_start_datetime):
    """ Function to get all the trading periods from the previous-day start datetime up to now 
        - We set the previous day two days ago in case we're close to the previous day"""
    
    # Set the previous day to two days before
    previous_day_start_datetime = previous_day_start_datetime - dt.timedelta(days=1)
    # Create a list of trading periods where the first value is the previous-day start datetime
    periods = [previous_day_start_datetime]
    
    # Fill the periods' list up to the now datetime
    i = 0
    while True:
       if (periods[i] + get_frequency_change(data_frequency)) <= now_:
           periods.append(periods[i] + get_frequency_change(data_frequency))
       else:
           break
       i += 1
       
    # Set the last period of the list as the next period from now
    periods.append(periods[-1] + get_frequency_change(data_frequency))

    return periods

def get_the_closest_periods(now_, data_frequency, trading_day_end_datetime, previous_day_start_datetime, day_start_datetime, market_close_time):
    """ Function to get the closest trading periods to now """
    
    # Get the periods' list
    periods = get_todays_periods(now_, data_frequency, previous_day_start_datetime)
    
    # If now is sooner than the trading day-end datetime
    if now_ < trading_day_end_datetime:
        # If the last periods' list datetime is sooner than the trading day-end datetime
        if periods[-1] <= trading_day_end_datetime:
            # The next period is the last datetime in the periods' list
            next_period = periods[-1]
        # If the last periods' list datetime is later than the trading day-end datetime
        else:
            # The next period is the trading_day_end_datetime
            next_period = trading_day_end_datetime
         
        # Set the previous and current period
        previous_period, current_period = periods[-3], periods[-2]
        
    # If now is sooner than the trading day start datetime
    elif now_ < day_start_datetime:
        # If the last periods' list datetime is sooner than the trading day-end datetime
        if periods[-1] <= day_start_datetime:
            # The next period is the last datetime in the periods' list
            next_period = periods[-1]
        # If the last periods' list datetime is later than the trading day-end datetime
        else:
            # The next period is the trading_day_end_datetime
            next_period = day_start_datetime
         
        # Set the previous and current period
        previous_period, current_period = periods[-3], periods[-2]
        
    # If now is sooner than the market close datetime
    elif now_ < market_close_time:
        # If the last periods' list datetime is sooner than the market close datetime
        if periods[-1] <= market_close_time:
            # The next period is the last datetime in the periods' list
            next_period = periods[-1]
        # If the last periods' list datetime is later than the market close datetime
        else:
            # The next period is the market close datetime
            next_period = market_close_time
            
        # Set the previous and current period
        previous_period, current_period = periods[-3], trading_day_end_datetime
    
    return previous_period, current_period, next_period

def allsaturdays(date0):
    """ Function to get all the Saturday dates from 2005 to date0 """
    # Create d to be looped
    d = date0
    # Get the next Saturday
    d += dt.timedelta(days = 5 - d.weekday())
    # Loop from d backwards up to 2005 (arbitrary year)
    while d.year >= 2005:
        # Return d
        yield d
        # Go backwards to the previous Saturday
        d -= dt.timedelta(days = 7)

def saturdays_list(date0): 
    """ Function to get all the Saturday datetimes from 2005 to date0 for the historical data download app"""
    # Get the Saturdays list
    saturdays = list(allsaturdays(date0))
    # Get half of the Saturdays list
    saturdays = saturdays[::2][:-1]
    # Convert the Saturdays to datetimes with 23:59:00 (arbitrarily chosen)
    saturdays = [datetime(date0.year, date0.month, date0.day, 23,59,0) for date0 in saturdays]
    # Convert the Saturdays datetimes to datetime type
    saturdays = [date0.strftime("%Y%m%d-%H:%M:%S") for date0 in saturdays]
    return saturdays