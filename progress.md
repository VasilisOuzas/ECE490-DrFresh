# Weekly Progress Log

Update this file every week.

## Week 1
### Completed
- Repository created
- Initial planning completed

### In Progress
- Architecture draft
- Task assignment

### Problems / Risks
- hardware delay

### Next Steps
- Finalize architecture
- Create first Issues

### Team Contribution
- Ούζας Βασίλειος
- Αλίνο Παπαγιάννης

## Week 2
### Completed
- Optimization of repository and some overdue merges
- Researched some topics on the communication layer and reconsidered our initial plans (nothing finilized yet)
- Finilized our choice on liquid tanks
- Finished MQTT and database part
- made first [demo](https://github.com/VasilisOuzas/ECE490-DrFresh/tree/main/demo-evidence)
- Made first steps on web page hosting GUI (but still it is not finished). 
### In Progress
- creation of a complete design for the actuation part
- Logic Implementation
- GUI
- PDF review
### Problems / Risks
- Both members of the team were absent this weekend. Not a lot of work was done this weekend and we need to catch up.
- Pumps are not working properly (They are spilling out the entire volume unintentionaly) -> Problem solved through basic Physics
### Next Steps
- Get some serius work done on the raspberry
- Get the first steps done on the logging/comunication with broker parts
- Manual dispensing
- GUI and visualization
- finilize script.py (main py file)
### Team Contribution
- Ούζας Βασίλειος
- Αλίνο Παπαγιάννης


## Week 3
### Completed
Connected the sensors and devices used in our project and verified that they
operate correctly.
Successfully used our first implementation of GUI (updates on /src/index.html and `/src/fresh_server.py`)
Successfully set up and used InfluxDB (check new version of `/src/script.py`)
Succesfully used InfluxDB for Grafana visualization
### In Progress
Finilization and optimization of GUI
Official ginal version of the physical implementation with actual tanks 
More testing and demo
### Problems / Risks
Our GUI implementation for the time being is using HTML, CSS, and JavaScript, hosted locally on the Raspberry Pi. There was a conflict in the team regarding the assignments obligations, wether we are forced or not to use MQTT protocol for the connection between front-end and back-end. We will take a closer look on the assignment instructions and come into contact with the Professor to find out if we can move on with our current implementation or ditch it for a new MQTT based version  
### Next Steps
Optimise every code and run more tests.
Take a deeper dive into our Grafana-InfluxDB usage

### Team Contribution
- Ούζας Βασίλειος
- Αλίνο Παπαγιάννης

## Week 4
### Completed
Finished the dashboard for the data visualization.
Added NGSI-LD context broker integration via Orion-LD
### In Progress

### Problems / Risks

### Next Steps

### Team Contribution
- Ούζας Βασίλειος
- Αλίνο Παπαγιάννης
