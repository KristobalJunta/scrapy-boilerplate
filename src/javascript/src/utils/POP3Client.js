import {Client} from 'yapople'
import pauseFor from "./pause";


class POP3Client {
  constructor(email, password, pop_config) {
    this._email = email;
    this._password = password;
    this._pop_config = pop_config;
  }

  async __checkMail(startTS) {
    return new Promise((resolve, reject) => {
      const client = new Client({
        hostname: this._pop_config.host,
        port: this._pop_config.port,
        tls: false,
        mailparser: true,
        username: this._email,
        password: this._password
      });

      const threshold = 60 * 1000;
      console.log('startTS:', startTS);
      // const threshold = 2 * 60 * 60 * 1000;

      client.connect(() => {
        client.count((countError, cnt) => {
          const messageNumbers = Array.from(Array(cnt).keys()).map(v => v + 1).reverse()
            .slice(0, Math.min(cnt, 10));
          console.log("messageNumbers", messageNumbers);
          client.retrieve(messageNumbers, (retrieveError, messages) => {
            console.log("messageNumbers: ", messages.length);
            const emailCode = messages.reverse().reduce((code, message) => {
              if (code !== null) {
                return code;
              }
              console.log(message.date.getTime(), (startTS - threshold), message.date.getTime() >= (startTS - threshold));
              if (message.date.getTime() >= (startTS - threshold)) {
                if (String(Array.from(message.from).shift().address).includes('verify@twitter')) {
                  console.log('verify@twitter msg to check')
                  const msgHTML = message.html;
                  const twitterCodeRegex = /<strong>([A-Za-z0-9]+)<\/strong><\/td>/gms;
                  const twitterRegexes = [twitterCodeRegex];

                  code = twitterRegexes.reduce((a, v) => {
                    if (a !== null) {
                      return a;
                    }
                    let twitterCode = v.exec(msgHTML);
                    if (twitterCode !== null && twitterCode.length > 1 && twitterCode[1] !== null) {
                      return twitterCode[1].trim();
                    }
                    return null;
                  }, null);
                }
              }
              return code;
            }, null);

            client.quit();
            return resolve(emailCode);
          })
        });
      });
    })
  };

  async getVerificationCode(startTS) {
    return new Promise(async (resolve, reject) => {
      this.resolve = resolve;
      this.reject = reject;

      // const startTS = new Date().getTime();
      const expirationTime = 60 * 1000;
      let currentTS = startTS;

      while (currentTS < (startTS + (2 * expirationTime))) {
        await pauseFor(5000);
        let emailCode = await this.__checkMail(startTS);
        if (emailCode !== null) {
          return resolve(emailCode);
        }
        currentTS = new Date().getTime();
        console.log("Tick: ", currentTS);
      }
      return reject(new Error("Task has been expired"));
    })
  }

}

module.exports = POP3Client;
