# weewx-windy

WeeWX extension that sends data to windy.com

You will need a STATION_ID and STATION_PASSWORD from windy.com

  https://stations.windy.com/

## Installation

1) download and install:

    WeeWX Version 4.x:
    
        wget -O weewx-windy.zip https://github.com/matthewwall/weewx-windy/archive/master.zip
        wee_extension --install weewx-windy.zip
    
    WeeWX Version 5.x:
    
        weectl extension install https://github.com/matthewwall/weewx-windy/archive/master.zip
 

2) enter parameters in the weewx configuration file

    ```
    [StdRESTful]
       [[Windy]]
           password = STATION_PASSWORD
           station = pws-station-001
   ```

3) restart weewx. For example:

    ```
    sudo /etc/init.d/weewx stop
    sudo /etc/init.d/weewx start
    ```

## License & Copyright
Copyright (c) 2019-2026 Matthew Wall

Distributed under the terms of the GNU Public License (GPLv3)
See the file LICENSE.txt for your full rights.

