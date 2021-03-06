import torch
from utils.web import *
from utils.text import *

## @package dialogpt
# Содержит класс чат-бота DialoGPT

## @class DialoGPT
# Класс, содержащий в себе интерфейс работы с DialoGPT
class DialoGPT:
    ## @var joke_classifier
    # Модель предсказания удачности высказывания
    joke_classifier = None
    ## @var has_swear
    # Классификатор наличия ненормативной лексики
    has_swear = None
    ## @var tokenizer
    # Токенайзер модели DialoGPT
    tokenizer = None
    ## @var model
    # Сама модель DialoGPT
    model = None
    ## @var config_reader
    # Объект, читающий конфигурацию для обработки команд
    config_reader = None
            
    ## Создаёт объект класса DialoGPT для отдельного чата
    # @param token Токен чат-бота
    # @param chat_id id чата
    # @param window_size Число диалогов, которых запоминает бот
    def __init__(self, token, chat_id, window_size=6):
        ## @var token
        # Токен, по которому бот может связаться с Telegram API
        self.token = token

        ## @var chat_id
        # Id чата, в котором общается бот
        self.chat_id = chat_id
        
        ## @var chat_history
        # Содержимое текущего диалога
        self.chat_history = []
        
        ## @var user_input_size
        # Размеры каждой фразы диалога
        self.user_input_size = []
        
        ## @var window_size
        # Число запоминаемых фраз в диалоге
        self.window_size = window_size

        ## @var trash
        # Если False, то применяется фильтр неприемлемой лексики
        self.trash = False

    def get_length_param(self, text: str) -> str:
        tokens_count = len(DialoGPT.tokenizer.encode(text))
        if tokens_count <= 15:
            len_param = '1'
        elif tokens_count <= 50:
            len_param = '2'
        elif tokens_count <= 256:
            len_param = '3'
        else:
            len_param = '-'
        return len_param
    
    ## Передаёт агрументы в функцию self.model.generate и вызывает её
    # @param bot_input_ids Токены, подаваемые self.model.generate на вход
    # @returns Токены, сгенерированные моделью
    def generate(self, bot_input_ids,
                num_return_sequences=5,
                max_length=2048,
                no_repeat_ngram_size=3,
                do_sample=True,
                top_k=50,
                top_p=0.9,
                temperature = 0.6,
                device='cuda'
    ):
        generated = [DialoGPT.model.generate(
                        bot_input_ids,
                        num_return_sequences=1,
                        max_length=max_length,
                        no_repeat_ngram_size=no_repeat_ngram_size,
                        do_sample=do_sample,
                        top_k=top_k,
                        top_p=top_p,
                        temperature = temperature,
                        mask_token_id=DialoGPT.tokenizer.mask_token_id,
                        eos_token_id=DialoGPT.tokenizer.eos_token_id,
                        unk_token_id=DialoGPT.tokenizer.unk_token_id,
                        pad_token_id=DialoGPT.tokenizer.pad_token_id,
                        device=device,
                    ) for _ in range(num_return_sequences)]# генерируем k возможных ответов
        
        ok_seq = [] # осуществляем отбор по признаку наличия мата
        for cur_chat_history in generated:
            decoded = DialoGPT.tokenizer.decode(cur_chat_history[:, bot_input_ids.shape[-1]:][0], skip_special_tokens=True)
            if self.trash or not DialoGPT.has_swear(decoded):
                ok_seq.append([cur_chat_history, decoded])
            else:
                print("We found swear here:", decoded) # logging

        results = [] # ранжируем оставшиеся предложения
        for cur_chat_history, cur_result in ok_seq:
            cur_score = DialoGPT.joke_classifier(cur_result)
            results.append([cur_chat_history, cur_result, cur_score])
        results.sort(key=lambda x:x[2])

        if len(results) == 0: # ответ по умолчанию если не знаем что сказать
            result = DialoGPT.config_reader.get_default_answer() 
            token = DialoGPT.tokenizer.encode(result, return_tensors="pt").cuda()
            results.append([token, result])
        
        self.chat_history = results[-1][0]

        return results[-1][1]
    
    ## Обрабатывает текст без команды
    # @param input_user Текст без команды
    # @param cnt_JokeClassifier Число итераций для поиска ответа, 0 - не использовать классификатор
    # @returns Ответ на текст
    def process_text(self, input_user, cnt_JokeClassifier=0):
        input_user = input_user[:256]
        
        # encode the new user input, add parameters and return a tensor in Pytorch
        new_user_input = f"|0|{self.get_length_param(input_user)}|" + input_user + DialoGPT.tokenizer.eos_token +  "|1|1|"
        new_user_input_ids = DialoGPT.tokenizer.encode(new_user_input, return_tensors="pt").cuda()
        
        # append the new user input tokens to the chat history
        bot_input_ids = torch.cat([self.chat_history, new_user_input_ids], dim=-1) if len(self.chat_history) else new_user_input_ids
        
        # generated a response
        result = self.generate(bot_input_ids, num_return_sequences=cnt_JokeClassifier)
        
        self.user_input_size.append(new_user_input_ids.shape[-1])
        if len(self.user_input_size) > self.window_size:
            self.chat_history = self.chat_history[:, self.user_input_size[0]:]
            self.user_input_size = self.user_input_size[-self.window_size:]
 
        print('Length of current chat history:', self.chat_history.shape[-1])
        return result
    
    ## Обрабатывает текст с возможной командой
    # @param text Текст, в котором может содержаться команда
    # @returns Итоговый текст
    def get_response(self, text):
        command = DialoGPT.config_reader(text)
        print('Executing command:', command)
        if command != None:
            return eval(command)

        ref = '/make_u_happy_bot'
        if text.startswith(ref):
            text = text[len(ref):]
        return self.process_text(text, cnt_JokeClassifier=5)
    
    ## Забывает все предыдущие фразы в диалоге
    # @returns Сообщение о рестарте
    def restart(self):
        self.chat_history = []
        self.user_input_size = []
        return 'Хорошо, начнём сначала'

    def switch_trash_mode(self):
        self.trash = not self.trash
        output = "Уберите детей от экранов"
        if not self.trash:
            output = "Можете звать детей обратно"
        return output