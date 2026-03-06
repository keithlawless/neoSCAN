# NeoSCAN Inuitive Software for Programming Uniden Radio Scanners

## What I want Claude to do

- Read this requirement documents
- Do NOT write any code yet
- Create a detailed plan in a new document called PLAN.md
- Recommend a programming language / framework to use for implementation
- The plan should be broken out into many phases that we can tackle one at a time
- If Claude wants to suggest additional features it can append them to the section "Claude's Ideas" below
- Create a CLAUDE.md file for this project that we can both contribute to once we figure out the approach we will take

## Requirements

- Create a cross-platform desktop application that will allow me to program my Uniden Radio Scanner
- I have a Uniden BCT15-X Scanner
- The MVP version of the software will support my scanner but future versions should support other models
- The application should have an intuitive UI with plenty of "help text" explaining what is going on
- The application will use a serial USB interface to connect to the scanner
- I have hooked up my scanner to this computer using the USB interface. The scanner is turned on ready to connect.
- In addition to programming the scanner the application should be able to "remote control" the scanner
- When the scanner is being "remote controlled" capture a log of transmissions the scanner is picking up including the source of the transmission (channel name), frequency, timestamp, and duration of transmission. 
- The application should be able to import files from FreeSCAN in "996" format
    - A sample file from FreeSCAN is in this project as ./sample-data/sample.996
- The application should be able to import files in the CSV format with intelligent field mapping based on the CSV file's header row

- Here are sources you can use to learn more about how to program my scanner:
1. Web site: http://new.marksscanners.com/BCT15X/bct15x.shtml
    - This is an operations manual for the BCT15X written by a hobbyist
2. Web site: http://new.marksscanners.com/index.shtml
    - This is an index of radio scanner web sites and sources
3. Web site: https://wiki.radioreference.com/index.php/Main_Page
    - This is community wiki for radio scanner enthusiasts
4. Web site: https://ukspec.tripod.com/rf/usc230e/dynamic.html
    - This site documents Uniden "Dynamic Memory" concept
5. PDF file: ./reference/BCD996XT_v1.04.00_Protocol.pdf
    - This PDF file describes the USB protocol used by Uniden radio scanners
4. Git repo: ../FreeSCAN
    - This is a local clone of open source software for programming a BCT15-X Scanner written in Visual Basic. It has been abandoned by the original author but should be consulted when creating a list of features to implement. It works but is very old and not very intuitive to use.
    
## Day Two Features (Not for this version)

- The application should be able to import radio scanner channel information from Radio Reference using their API. Require the user to have a paid account. Don't just scrape their web site. 
    - API details are documented here: https://wiki.radioreference.com/index.php/API
    - I need to be obtain an API key. I will provide that when I have one.

## Claude's Ideas

- Claude can put it's ideas for new features here