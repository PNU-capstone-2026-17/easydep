# Few-shot 예시 샘플링 실험 결과

- 데이터셋: `materials/FR_NFR_Dataset/FR_NFR_Dataset.xlsx` (5999개 요구사항, 결측/중복 제거 후)
- NIM 임베딩 모델: `nvidia/llama-nemotron-embed-1b-v2` (2048-d)
- top-N = 5, random seed = 42
- 지표: **관련성**(mean_query_sim, 쿼리와 선택 예시의 평균 코사인 ↑좋음) / **다양성**(diversity, 선택 예시 간 평균 코사인 ↓다양)

## Q1. "I want to build a shopping mall service."

| 구성 | 관련성 | 다양성 |
|---|---|---|
| random | 0.1190 | 0.3441 |
| cosine+tfidf | 0.2362 | 0.1418 |
| cosine+nim | 0.2666 | 0.6270 |
| mmr+tfidf | 0.2018 | 0.0285 |
| mmr+nim | 0.2475 | 0.4344 |

**random** (관련성 0.1190, 다양성 0.3441)

- `[+0.055]` Administrator shall be able to print reports (annually, weekly, daily).
- `[+0.142]` User shall have the option to modify existing tickets, including parameters such as time, date, route, and availability.
- `[+0.118]` System shall provide an archetype to help dataset developers package dataset types properly.
- `[+0.167]` The system shall be able to update inventory automatically upon user receipt of a requested asset.
- `[+0.113]` Sales managers shall view customer details and manage product sales and deliveries.

**cosine+tfidf** (관련성 0.2362, 다양성 0.1418)

- `[+0.272]` System shall be able to build URLs for each sites page.
- `[+0.249]` User shall add items to the shopping cart with a response time of less than 1 second, enhancing the shopping experience.
- `[+0.223]` The system shall disclose security policies and practices to build user trust.
- `[+0.219]` Customers shall add products to the shopping cart.
- `[+0.217]` System shall feature an API for customers to build custom plug-ins.

**cosine+nim** (관련성 0.2666, 다양성 0.6270)

- `[+0.276]` The service shall be able to show the seating arrangement and available seats, allowing users to book the desired number of seats.
- `[+0.276]` User shall/will be able to access online shopping opportunities.
- `[+0.270]` System shall be able to enable access to online shopping opportunities.
- `[+0.256]` System shall provide a shopping cart during online purchase.
- `[+0.256]` User shall be able to request transportation routes.

**mmr+tfidf** (관련성 0.2018, 다양성 0.0285)

- `[+0.272]` System shall be able to build URLs for each sites page.
- `[+0.249]` User shall add items to the shopping cart with a response time of less than 1 second, enhancing the shopping experience.
- `[+0.189]` Users shall be able to book a movie through the service.
- `[+0.164]` The Exit menu allows the user to confirm whether they want to continue using the application or exit.
- `[+0.134]` The administrator shall have the ability to specify which diagnostic results will not be reported in any manner, i.e., not detected.

**mmr+nim** (관련성 0.2475, 다양성 0.4344)

- `[+0.276]` The service shall be able to show the seating arrangement and available seats, allowing users to book the desired number of seats.
- `[+0.236]` System shall require shop owners to obtain permission from the administrator before selling products.
- `[+0.276]` User shall/will be able to access online shopping opportunities.
- `[+0.225]` This program shall be your one-stop shop for getting things done and achieving peace of mind.
- `[+0.225]` The ATM shall provide customers with 24-hour service for consistent availability.

---

## Q2. "The app should let users chat with each other in real time."

| 구성 | 관련성 | 다양성 |
|---|---|---|
| random | 0.1561 | 0.3441 |
| cosine+tfidf | 0.4768 | 0.2879 |
| cosine+nim | 0.4922 | 0.5647 |
| mmr+tfidf | 0.4538 | 0.2173 |
| mmr+nim | 0.4845 | 0.5143 |

**random** (관련성 0.1561, 다양성 0.3441)

- `[+0.132]` Administrator shall be able to print reports (annually, weekly, daily).
- `[+0.252]` User shall have the option to modify existing tickets, including parameters such as time, date, route, and availability.
- `[+0.127]` System shall provide an archetype to help dataset developers package dataset types properly.
- `[+0.244]` The system shall be able to update inventory automatically upon user receipt of a requested asset.
- `[+0.025]` Sales managers shall view customer details and manage product sales and deliveries.

**cosine+tfidf** (관련성 0.4768, 다양성 0.2879)

- `[+0.749]` User's will be able to chat with each other in real time.
- `[+0.557]` The MultiMahjongServer will allow players on MultiMahjongClient programs to chat with each other in real time.
- `[+0.383]` User shall integrate the app with communication platforms for real-time translation of conversations, chat messages, and social media posts.
- `[+0.359]` The system shall allow users to chat with other users who are collaborating on a score.
- `[+0.336]` Users shall be able to send messages to each other.

**cosine+nim** (관련성 0.4922, 다양성 0.5647)

- `[+0.579]` User's will be able to chat with each other in real time.
- `[+0.535]` User shall integrate the app with communication platforms for real-time translation of conversations, chat messages, and social media posts.
- `[+0.471]` The system shall support real-time messaging and notifications for instant communication.
- `[+0.445]` User shall seamlessly translate text input from one language to another in real-time, preserving meaning and conveying nuance effectively.
- `[+0.431]` The system shall allow users to chat with other users who are collaborating on a score.

**mmr+tfidf** (관련성 0.4538, 다양성 0.2173)

- `[+0.749]` User's will be able to chat with each other in real time.
- `[+0.557]` The MultiMahjongServer will allow players on MultiMahjongClient programs to chat with each other in real time.
- `[+0.383]` User shall integrate the app with communication platforms for real-time translation of conversations, chat messages, and social media posts.
- `[+0.359]` The system shall allow users to chat with other users who are collaborating on a score.
- `[+0.221]` The system shall provide uninterrupted functionality both with and without an internet connection. In offline mode, users should still be able to access certain features. In online mode, the system should operate smoothly.

**mmr+nim** (관련성 0.4845, 다양성 0.5143)

- `[+0.579]` User's will be able to chat with each other in real time.
- `[+0.535]` User shall integrate the app with communication platforms for real-time translation of conversations, chat messages, and social media posts.
- `[+0.408]` User shall be able to view real-time location maps with friends' Bitmoji representations, accessible by swiping down on the camera screen.
- `[+0.471]` The system shall support real-time messaging and notifications for instant communication.
- `[+0.429]` User shall be able to view a very clear map showing the buildings and rooms, so that they can make it to class on time.

---

## Q3. "We need a system to manage employee attendance and payroll."

| 구성 | 관련성 | 다양성 |
|---|---|---|
| random | 0.1975 | 0.3441 |
| cosine+tfidf | 0.3664 | 0.2478 |
| cosine+nim | 0.4170 | 0.5567 |
| mmr+tfidf | 0.3356 | 0.1125 |
| mmr+nim | 0.4067 | 0.5274 |

**random** (관련성 0.1975, 다양성 0.3441)

- `[+0.218]` Administrator shall be able to print reports (annually, weekly, daily).
- `[+0.176]` User shall have the option to modify existing tickets, including parameters such as time, date, route, and availability.
- `[+0.153]` System shall provide an archetype to help dataset developers package dataset types properly.
- `[+0.276]` The system shall be able to update inventory automatically upon user receipt of a requested asset.
- `[+0.164]` Sales managers shall view customer details and manage product sales and deliveries.

**cosine+tfidf** (관련성 0.3664, 다양성 0.2478)

- `[+0.407]` The Employee menu allows the user to access staff entry and attendance details.
- `[+0.374]` System shall provide the administrator with access to view all employee details and manage the site.
- `[+0.372]` User (teacher) shall be able to mark, view, and update students' attendance; the system shall display attendance to the respective student, and students shall be able to download attendance reports from the system.
- `[+0.357]` System shall enable the administrator to manage the database and track all customer and employee records.
- `[+0.321]` Employee shall log into the system.

**cosine+nim** (관련성 0.4170, 다양성 0.5567)

- `[+0.431]` System shall be able to enable employees to select their availability.
- `[+0.428]` System shall enable the administrator to manage the database and track all customer and employee records.
- `[+0.418]` System shall monitor user attendance data and send notifications for tardiness or absences.
- `[+0.417]` The Employee menu allows the user to access staff entry and attendance details.
- `[+0.391]` User (teacher) shall be able to mark, view, and update students' attendance; the system shall display attendance to the respective student, and students shall be able to download attendance reports from the system.

**mmr+tfidf** (관련성 0.3356, 다양성 0.1125)

- `[+0.407]` The Employee menu allows the user to access staff entry and attendance details.
- `[+0.357]` System shall enable the administrator to manage the database and track all customer and employee records.
- `[+0.372]` User (teacher) shall be able to mark, view, and update students' attendance; the system shall display attendance to the respective student, and students shall be able to download attendance reports from the system.
- `[+0.234]` User will need a device capable of playing a 144p resolution video.
- `[+0.308]` Employee shall select availability.

**mmr+nim** (관련성 0.4067, 다양성 0.5274)

- `[+0.431]` System shall be able to enable employees to select their availability.
- `[+0.417]` The Employee menu allows the user to access staff entry and attendance details.
- `[+0.391]` User (teacher) shall be able to mark, view, and update students' attendance; the system shall display attendance to the respective student, and students shall be able to download attendance reports from the system.
- `[+0.418]` System shall monitor user attendance data and send notifications for tardiness or absences.
- `[+0.377]` System shall present the employee homepage for inputting duty slip information, calculating net bills, and updating car status and customer database.

---

## Q4. "Make a platform where people can book hotels and flights."

| 구성 | 관련성 | 다양성 |
|---|---|---|
| random | 0.1185 | 0.3441 |
| cosine+tfidf | 0.2673 | 0.1013 |
| cosine+nim | 0.3659 | 0.5959 |
| mmr+tfidf | 0.2650 | 0.0814 |
| mmr+nim | 0.3171 | 0.3789 |

**random** (관련성 0.1185, 다양성 0.3441)

- `[+0.047]` Administrator shall be able to print reports (annually, weekly, daily).
- `[+0.261]` User shall have the option to modify existing tickets, including parameters such as time, date, route, and availability.
- `[+0.071]` System shall provide an archetype to help dataset developers package dataset types properly.
- `[+0.117]` The system shall be able to update inventory automatically upon user receipt of a requested asset.
- `[+0.097]` Sales managers shall view customer details and manage product sales and deliveries.

**cosine+tfidf** (관련성 0.2673, 다양성 0.1013)

- `[+0.341]` System shall enable users to search and book hotels by specifying place and date, providing a list of available hotels with cost information, and redirecting to the payment gateway when booking.
- `[+0.296]` User shall be able to search for flights by entering destination, boarding place, and date of journey, with a display of available flights matching the criteria.
- `[+0.269]` The system shall provide a social messaging platform where users can create and manage groups with chat functionalities.
- `[+0.221]` System shall maintain the book status as Pending when a member requests a book.
- `[+0.211]` The user shall make a request to reserve a book, which shall then be sent to the administrator for approval.

**cosine+nim** (관련성 0.3659, 다양성 0.5959)

- `[+0.392]` System shall enable users to search and book hotels by specifying place and date, providing a list of available hotels with cost information, and redirecting to the payment gateway when booking.
- `[+0.369]` System shall allow users to search and book tours by specifying place and number of travelers, providing tour plans with costs, destinations, and accommodations, with redirection to payment gateway for booking.
- `[+0.364]` User shall be able to make a booking (client function).
- `[+0.357]` The user shall be able to cancel booked tickets if they need to change their travel dates.
- `[+0.349]` The service shall be able to show the seating arrangement and available seats, allowing users to book the desired number of seats.

**mmr+tfidf** (관련성 0.2650, 다양성 0.0814)

- `[+0.341]` System shall enable users to search and book hotels by specifying place and date, providing a list of available hotels with cost information, and redirecting to the payment gateway when booking.
- `[+0.269]` The system shall provide a social messaging platform where users can create and manage groups with chat functionalities.
- `[+0.296]` User shall be able to search for flights by entering destination, boarding place, and date of journey, with a display of available flights matching the criteria.
- `[+0.209]` Users shall be able to send messages to a team of people.
- `[+0.211]` The user shall make a request to reserve a book, which shall then be sent to the administrator for approval.

**mmr+nim** (관련성 0.3171, 다양성 0.3789)

- `[+0.392]` System shall enable users to search and book hotels by specifying place and date, providing a list of available hotels with cost information, and redirecting to the payment gateway when booking.
- `[+0.273]` The platform shall have a blog highlighting open fiscal projects.
- `[+0.357]` The user shall be able to cancel booked tickets if they need to change their travel dates.
- `[+0.324]` The booking process will be intuitive, with information completion in stages.
- `[+0.241]` The Khan Academy platform shall be easy to use for students and teachers with varying levels of technical experience.

---

## Q5. "The website must be secure and protect customer data."

| 구성 | 관련성 | 다양성 |
|---|---|---|
| random | 0.1589 | 0.3441 |
| cosine+tfidf | 0.5027 | 0.5040 |
| cosine+nim | 0.4972 | 0.5442 |
| mmr+tfidf | 0.3893 | 0.1269 |
| mmr+nim | 0.4972 | 0.5442 |

**random** (관련성 0.1589, 다양성 0.3441)

- `[+0.116]` Administrator shall be able to print reports (annually, weekly, daily).
- `[+0.160]` User shall have the option to modify existing tickets, including parameters such as time, date, route, and availability.
- `[+0.146]` System shall provide an archetype to help dataset developers package dataset types properly.
- `[+0.193]` The system shall be able to update inventory automatically upon user receipt of a requested asset.
- `[+0.180]` Sales managers shall view customer details and manage product sales and deliveries.

**cosine+tfidf** (관련성 0.5027, 다양성 0.5040)

- `[+0.707]` The system shall be secure and protect user data.
- `[+0.484]` The system shall be secure and protect user data with appropriate access controls.
- `[+0.480]` The system should be secure to protect user data and prevent unauthorized access.
- `[+0.453]` The system shall be secure and protect user data throughout the cloning process.
- `[+0.390]` The system shall be secure and protect user privacy by following data security best practices.

**cosine+nim** (관련성 0.4972, 다양성 0.5442)

- `[+0.546]` The developed website must be highly reliable and secure to prevent any leakage of applicant information.
- `[+0.508]` The website shall utilize HTTPS protocol to enhance security during data transmission.
- `[+0.480]` The website must be capable of storing all data formats and should frequently connect to the database or be database sensitive.
- `[+0.478]` User shall have secure access to confidential data (user's details) using SSL encryption.
- `[+0.473]` The learning website shall implement security measures to prevent unauthorized access, modification, or deletion of data.

**mmr+tfidf** (관련성 0.3893, 다양성 0.1269)

- `[+0.707]` The system shall be secure and protect user data.
- `[+0.342]` The developed website must be highly reliable and secure to prevent any leakage of applicant information.
- `[+0.274]` The website must be capable of storing all data formats and should frequently connect to the database or be database sensitive.
- `[+0.352]` System's back-end databases shall be encrypted to protect customer data.
- `[+0.272]` The passport management website must be available 24 hours a day.

**mmr+nim** (관련성 0.4972, 다양성 0.5442)

- `[+0.546]` The developed website must be highly reliable and secure to prevent any leakage of applicant information.
- `[+0.480]` The website must be capable of storing all data formats and should frequently connect to the database or be database sensitive.
- `[+0.478]` User shall have secure access to confidential data (user's details) using SSL encryption.
- `[+0.473]` The learning website shall implement security measures to prevent unauthorized access, modification, or deletion of data.
- `[+0.508]` The website shall utilize HTTPS protocol to enhance security during data transmission.

---

## Q6. "Build a mobile app for tracking daily fitness and workouts."

| 구성 | 관련성 | 다양성 |
|---|---|---|
| random | 0.1070 | 0.3441 |
| cosine+tfidf | 0.2605 | 0.0897 |
| cosine+nim | 0.4612 | 0.6755 |
| mmr+tfidf | 0.2502 | 0.0362 |
| mmr+nim | 0.4409 | 0.5635 |

**random** (관련성 0.1070, 다양성 0.3441)

- `[+0.151]` Administrator shall be able to print reports (annually, weekly, daily).
- `[+0.148]` User shall have the option to modify existing tickets, including parameters such as time, date, route, and availability.
- `[+0.047]` System shall provide an archetype to help dataset developers package dataset types properly.
- `[+0.111]` The system shall be able to update inventory automatically upon user receipt of a requested asset.
- `[+0.078]` Sales managers shall view customer details and manage product sales and deliveries.

**cosine+tfidf** (관련성 0.2605, 다양성 0.0897)

- `[+0.307]` The system shall offer a mobile app for computers and mobile devices for accessing OneDrive.
- `[+0.269]` System shall be able to build URLs for each sites page.
- `[+0.249]` The system should be accessible on various devices (mobile app implies mobile accessibility).
- `[+0.244]` User shall be able to integrate their Strava account or Fitbit watch to view fitness-related information within the CrossFit app.
- `[+0.233]` System shall allow users to enter order information for tracking.

**cosine+nim** (관련성 0.4612, 다양성 0.6755)

- `[+0.501]` User shall be able to record exercises, repetitions, and the followed diet within the application.
- `[+0.467]` User shall share their progress within the app and have the ability to copy custom workout sessions.
- `[+0.465]` User shall have their daily calorie goals recorded, and reminders will be sent if a workout is missed.
- `[+0.440]` User shall have the option to create a customized workout plan, with a detailed graph of the workout displayed.
- `[+0.433]` User shall be able to select from various workout sessions available in the app.

**mmr+tfidf** (관련성 0.2502, 다양성 0.0362)

- `[+0.307]` The system shall offer a mobile app for computers and mobile devices for accessing OneDrive.
- `[+0.269]` System shall be able to build URLs for each sites page.
- `[+0.244]` User shall be able to integrate their Strava account or Fitbit watch to view fitness-related information within the CrossFit app.
- `[+0.233]` System shall allow users to enter order information for tracking.
- `[+0.198]` Employee shall maintain daily logs.

**mmr+nim** (관련성 0.4409, 다양성 0.5635)

- `[+0.501]` User shall be able to record exercises, repetitions, and the followed diet within the application.
- `[+0.465]` User shall have their daily calorie goals recorded, and reminders will be sent if a workout is missed.
- `[+0.467]` User shall share their progress within the app and have the ability to copy custom workout sessions.
- `[+0.440]` User shall have the option to create a customized workout plan, with a detailed graph of the workout displayed.
- `[+0.331]` The application should be designed for easy porting between iOS and Android platforms.

---

## Q7. "I want an online learning platform with video courses and quizzes."

| 구성 | 관련성 | 다양성 |
|---|---|---|
| random | 0.0591 | 0.3441 |
| cosine+tfidf | 0.2115 | 0.1185 |
| cosine+nim | 0.3230 | 0.6642 |
| mmr+tfidf | 0.2040 | 0.0246 |
| mmr+nim | 0.2879 | 0.3820 |

**random** (관련성 0.0591, 다양성 0.3441)

- `[+0.067]` Administrator shall be able to print reports (annually, weekly, daily).
- `[+0.118]` User shall have the option to modify existing tickets, including parameters such as time, date, route, and availability.
- `[+0.019]` System shall provide an archetype to help dataset developers package dataset types properly.
- `[+0.086]` The system shall be able to update inventory automatically upon user receipt of a requested asset.
- `[+0.005]` Sales managers shall view customer details and manage product sales and deliveries.

**cosine+tfidf** (관련성 0.2115, 다양성 0.1185)

- `[+0.302]` Students shall be able to take quizzes.
- `[+0.202]` User shall be able to view video details such as video length and size by entering the video name.
- `[+0.197]` Students shall be able to choose courses.
- `[+0.180]` The platform shall experience minimal to no buffering during video playback.
- `[+0.176]` Administrator shall be able to create users, departments, and courses.

**cosine+nim** (관련성 0.3230, 다양성 0.6642)

- `[+0.336]` The Khan Academy platform shall provide opportunities for students to learn independently.
- `[+0.329]` The Khan Academy platform shall provide students with immediate feedback on their performance.
- `[+0.326]` The Khan Academy platform shall be engaging for students.
- `[+0.316]` Students shall be able to take quizzes.
- `[+0.308]` The Khan Academy platform shall provide students with opportunities to practice and refine math skills.

**mmr+tfidf** (관련성 0.2040, 다양성 0.0246)

- `[+0.302]` Students shall be able to take quizzes.
- `[+0.202]` User shall be able to view video details such as video length and size by entering the video name.
- `[+0.175]` The system shall only allow registered users to access courses.
- `[+0.173]` The e-learning tool shall be compatible with both Windows and Linux operating systems.
- `[+0.168]` The user shall be able to request an exchange for an item they want, if the item they have is eligible for exchange.

**mmr+nim** (관련성 0.2879, 다양성 0.3820)

- `[+0.336]` The Khan Academy platform shall provide opportunities for students to learn independently.
- `[+0.285]` The system shall allow faculty to upload course materials (syllabus, lesson plans, notes, etc.) and question banks.
- `[+0.279]` User shall ensure that the learning time for the application is between 2 to 4 hours, catering to working students.
- `[+0.270]` The user shall be able to check a box on "Resit" if they are giving the exam again.
- `[+0.269]` User shall have a training landing page with an intro section and a list of all training offerings, to easily shop for training.

---

## Q8. "The system should recommend products based on what users like."

| 구성 | 관련성 | 다양성 |
|---|---|---|
| random | 0.1761 | 0.3441 |
| cosine+tfidf | 0.3028 | 0.1734 |
| cosine+nim | 0.4558 | 0.6663 |
| mmr+tfidf | 0.2746 | 0.0804 |
| mmr+nim | 0.4350 | 0.5265 |

**random** (관련성 0.1761, 다양성 0.3441)

- `[+0.094]` Administrator shall be able to print reports (annually, weekly, daily).
- `[+0.154]` User shall have the option to modify existing tickets, including parameters such as time, date, route, and availability.
- `[+0.218]` System shall provide an archetype to help dataset developers package dataset types properly.
- `[+0.315]` The system shall be able to update inventory automatically upon user receipt of a requested asset.
- `[+0.100]` Sales managers shall view customer details and manage product sales and deliveries.

**cosine+tfidf** (관련성 0.3028, 다양성 0.1734)

- `[+0.329]` System shall recommend similar items based on the user's buying pattern.
- `[+0.328]` The system shall recommend videos based on uploaded pictures or geographical location.
- `[+0.302]` The system shall recommend content based on user profiles, news events, and image/video similarity.
- `[+0.283]` The user interface should be GUI based.
- `[+0.272]` User shall enter search text on the screen and display matching products based on the search.

**cosine+nim** (관련성 0.4558, 다양성 0.6663)

- `[+0.527]` System shall recommend similar items based on the user's buying pattern.
- `[+0.455]` System shall display related items to the items already bought by the user.
- `[+0.455]` The system shall recommend content based on user profiles, news events, and image/video similarity.
- `[+0.434]` System shall display all related items that other users purchased in addition to the selected product.
- `[+0.408]` System shall be able to make predictions as top 5 recommendations relevant to the user's data.

**mmr+tfidf** (관련성 0.2746, 다양성 0.0804)

- `[+0.329]` System shall recommend similar items based on the user's buying pattern.
- `[+0.242]` The system shall define what data can be entered, how it should be formatted, and any rules for validating the data.
- `[+0.248]` The system shall allow users to comment on posts they like.
- `[+0.283]` The user interface should be GUI based.
- `[+0.272]` User shall enter search text on the screen and display matching products based on the search.

**mmr+nim** (관련성 0.4350, 다양성 0.5265)

- `[+0.527]` System shall recommend similar items based on the user's buying pattern.
- `[+0.397]` System shall update recommendations at the end of listening to a track or after purchasing a track, aiming for real-time functionality.
- `[+0.455]` The system shall recommend content based on user profiles, news events, and image/video similarity.
- `[+0.400]` System shall customize the user's homepage based on previous interactions, including wish-list additions, page views, and previous searches.
- `[+0.396]` The system shall allow users to comment on posts they like.

---

## Q9. "Create a food delivery service that shows nearby restaurants."

| 구성 | 관련성 | 다양성 |
|---|---|---|
| random | 0.1283 | 0.3441 |
| cosine+tfidf | 0.2874 | 0.3973 |
| cosine+nim | 0.5021 | 0.7339 |
| mmr+tfidf | 0.2284 | 0.0480 |
| mmr+nim | 0.4711 | 0.6139 |

**random** (관련성 0.1283, 다양성 0.3441)

- `[+0.100]` Administrator shall be able to print reports (annually, weekly, daily).
- `[+0.176]` User shall have the option to modify existing tickets, including parameters such as time, date, route, and availability.
- `[+0.121]` System shall provide an archetype to help dataset developers package dataset types properly.
- `[+0.160]` The system shall be able to update inventory automatically upon user receipt of a requested asset.
- `[+0.085]` Sales managers shall view customer details and manage product sales and deliveries.

**cosine+tfidf** (관련성 0.2874, 다양성 0.3973)

- `[+0.343]` Customers shall view nearby restaurants within a specified distance.
- `[+0.338]` Customer shall be able to view nearby restaurants(Specified Distance).
- `[+0.259]` Delivery boys shall receive customer details from restaurants.
- `[+0.257]` Customers shall be able to rate restaurants and delivery boys.
- `[+0.240]` Restaurants shall receive acknowledgment from delivery boys.

**cosine+nim** (관련성 0.5021, 다양성 0.7339)

- `[+0.528]` Customer shall be able to view nearby restaurants(Specified Distance).
- `[+0.515]` User shall be able to search for restaurants by entering a desired distance range (minimum and maximum) from their current location.
- `[+0.492]` The user shall be able to choose a restaurant type from a list and see the results on a map.
- `[+0.491]` Customers shall view nearby restaurants within a specified distance.
- `[+0.485]` The user shall be able to search for restaurants by any criteria (name, description, address, type, or menu) in a free-text field, with results shown on a map.

**mmr+tfidf** (관련성 0.2284, 다양성 0.0480)

- `[+0.343]` Customers shall view nearby restaurants within a specified distance.
- `[+0.259]` Delivery boys shall receive customer details from restaurants.
- `[+0.177]` User shall be able to view the latest movies and television shows by clicking "Latest."
- `[+0.201]` Customers shall order food and add items to their cart.
- `[+0.162]` Users shall be able to book a movie through the service.

**mmr+nim** (관련성 0.4711, 다양성 0.6139)

- `[+0.528]` Customer shall be able to view nearby restaurants(Specified Distance).
- `[+0.485]` The user shall be able to search for restaurants by any criteria (name, description, address, type, or menu) in a free-text field, with results shown on a map.
- `[+0.515]` User shall be able to search for restaurants by entering a desired distance range (minimum and maximum) from their current location.
- `[+0.485]` The user shall be able to choose a dish from a list and see results on a map.
- `[+0.343]` Admin shall be able to facilitate contact between delivery boys and restaurants to replace or cancel orders if items are unavailable.

---

## Q10. "We want a dashboard that shows sales analytics in real time."

| 구성 | 관련성 | 다양성 |
|---|---|---|
| random | 0.1980 | 0.3441 |
| cosine+tfidf | 0.2343 | 0.2196 |
| cosine+nim | 0.3593 | 0.5433 |
| mmr+tfidf | 0.1925 | 0.0274 |
| mmr+nim | 0.3428 | 0.4294 |

**random** (관련성 0.1980, 다양성 0.3441)

- `[+0.269]` Administrator shall be able to print reports (annually, weekly, daily).
- `[+0.155]` User shall have the option to modify existing tickets, including parameters such as time, date, route, and availability.
- `[+0.149]` System shall provide an archetype to help dataset developers package dataset types properly.
- `[+0.189]` The system shall be able to update inventory automatically upon user receipt of a requested asset.
- `[+0.228]` Sales managers shall view customer details and manage product sales and deliveries.

**cosine+tfidf** (관련성 0.2343, 다양성 0.2196)

- `[+0.240]` System shall provide real-time price updates.
- `[+0.239]` System shall provide tools for tracking and analyzing sales performance, including metrics such as total sales, top-selling medicines, and sales trends over time.
- `[+0.238]` User shall be able to track and analyze sales performance, including total sales, top-selling medicines, and sales trends.
- `[+0.228]` User's will be able to chat with each other in real time.
- `[+0.227]` Sales managers shall view customer details and manage product sales and deliveries.

**cosine+nim** (관련성 0.3593, 다양성 0.5433)

- `[+0.375]` User shall be able to track and analyze sales performance, including total sales, top-selling medicines, and sales trends.
- `[+0.372]` System shall provide tools for tracking and analyzing sales performance, including metrics such as total sales, top-selling medicines, and sales trends over time.
- `[+0.357]` The manager shall view sales statistics in various formats such as pie charts, bar graphs, and tabular formats.
- `[+0.349]` Admin shall have a dashboard showing usage stats and locations for insights.
- `[+0.342]` The system shall enable users to generate reports and analyze sales data and marketing trends.

**mmr+tfidf** (관련성 0.1925, 다양성 0.0274)

- `[+0.240]` System shall provide real-time price updates.
- `[+0.238]` User shall be able to track and analyze sales performance, including total sales, top-selling medicines, and sales trends.
- `[+0.186]` The user shall be able to view the Menu from the dashboard.
- `[+0.170]` The system shall offer built-in analytics to track marketing activities, user sessions, engagement metrics, and campaign performance.
- `[+0.129]` Seamlessness shall be accomplished in a manner that is seamless, in that it does not affect hardware modules or software functionality that it meets at interfaces.

**mmr+nim** (관련성 0.3428, 다양성 0.4294)

- `[+0.375]` User shall be able to track and analyze sales performance, including total sales, top-selling medicines, and sales trends.
- `[+0.357]` The manager shall view sales statistics in various formats such as pie charts, bar graphs, and tabular formats.
- `[+0.313]` System shall update user stats in real time after every achievement or completed action for an enhanced gaming experience.
- `[+0.319]` The system shall offer a meeting scheduling tool and live chat functionality to connect with prospects and customers.
- `[+0.349]` Admin shall have a dashboard showing usage stats and locations for insights.

---

## 전체 평균 (10문장)

| 구성 | 평균 관련성 | 평균 다양성 |
|---|---|---|
| random | 0.1419 | 0.3441 |
| cosine+tfidf | 0.3146 | 0.2281 |
| cosine+nim | 0.4140 | 0.6172 |
| mmr+tfidf | 0.2795 | 0.0783 |
| mmr+nim | 0.3931 | 0.4915 |

> 해석: random 은 관련성이 가장 낮다(baseline). cosine 은 관련성 최고이나 top-k 특성상 다양성이 떨어질 수 있고, mmr 은 관련성을 약간 양보하는 대신 다양성을 확보한다. tfidf(어휘) vs nim(의미)의 차이도 표에서 비교 가능하다.
