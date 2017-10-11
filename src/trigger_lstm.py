# This file mainly implements a basic attention model for neural event extraction
# input file consists of sentences which contain one-event.
import os
import re
import sys
import time
import random
import cPickle
from collections import Counter
from aceEventUtil import loadEventHierarchy
#from get_constituent_topdown_oracle import unkify
from util import outputPRF, outputParameters
from util import loadVocab, loadTag, loadTrainData, loadPretrain
from util import loadTrainData2, loadPretrain2
from util import output_normal_pretrain, output_dynet_format
from util import check_trigger, check_trigger_test, check_data
from util import get_trigger, evalPRF, evalPRF_iden
from args import get_args

import numpy as np
from nltk.tokenize import sent_tokenize, word_tokenize

import torch
import torch.autograd as autograd
from torch.autograd import Variable
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import torch.utils.data as torch_data

from lstm_trigger import LSTMTrigger
torch.manual_seed(1)
Tab = "\t"

def prepare_sequence(seq, to_ix):
    idxs = [to_ix[w] if w in to_ix else len(to_ix)-1 for w in seq]
    tensor = autograd.Variable(torch.LongTensor(idxs), requires_grad=False)
    return tensor

def arr2tensor(arr):
    tensor = autograd.Variable(torch.LongTensor(arr), requires_grad=False)
    return tensor

def eval_model(data, model, loss_function, data_flag, gpu):
    debug = False
    loss_all = 0
    gold_results = []
    pred_results = []
    for sent, tags, gold_triggers in data[:]:

        sentence_in = arr2tensor(sent)
        targets = arr2tensor(tags)
        iden_tags = [1 if tag != 0 else tag for tag in tags]
        iden_targets = arr2tensor(iden_tags)

        if gpu:
            sentence_in = sentence_in.cuda()
            targets = targets.cuda()
            iden_targets = iden_targets.cuda()

        tag_space, tag_scores, tag_space_iden, tag_scores_iden = model(sentence_in, gpu)

        _, tag_outputs = tag_scores.data.max(1)
        if gpu: tag_outputs = tag_outputs.cpu()
        if debug:
            if len(gold_results) in range(10, 11):
                print "-tag scores", tag_scores.data.size(), tag_scores.data[:1,], tag_scores.data[:1,].max(1)
                print "-tag output", tag_outputs.numpy().tolist()

        sys_triggers = get_trigger(tag_outputs.view(len(tags)).numpy().tolist())

        gold_results.append(gold_triggers)
        pred_results.append(sys_triggers)

        if debug and data_flag == "train":
            if len(gold_results) in range(10, 11):
            #if len(gold_results) in range(0, 15):
                if len(gold_triggers) == 0: continue
                print "-gold tag", gold_triggers
                print "-out tag", sys_triggers
        if 1:
            loss = loss_function(tag_scores, targets) + loss_function(tag_scores_iden, iden_targets)
        else:
            loss = loss_function(tag_space, targets)# + loss_function(tag_space_iden, iden_targets)
        loss_all += loss.data[0]
    #print "## gold out", gold_results
    #print "## sys out", pred_results
    prf = evalPRF(gold_results, pred_results, data_flag)
    prf_iden = evalPRF_iden(gold_results, pred_results)
    return loss_all, prf, prf_iden

def load_data2(args_arr):
    debug = True

    pretrain_embedding, pretrain_vocab = loadPretrain2(args.pretrain_embed)
    print "## pretrained embedding loaded.", time.asctime(), pretrain_embedding.shape

    training_data = loadTrainData2(args.train)
    print "## train loaded.", args.train, time.asctime()
    dev_data = loadTrainData2(args.dev)
    print "## dev loaded.", args.dev, time.asctime()
    test_data = loadTrainData2(args.test)
    print "## test loaded.", args.test, time.asctime()
    all_data = training_data + dev_data + test_data
    vocab = sorted(list(set([word_text for sent_text, _ in all_data for word_text in sent_text])))
    vocab = dict(zip(vocab, range(len(vocab))))
    vocab["<unk>"] = len(vocab)
    #pretrain_vocab = vocab
    #pretrain_embedding = np.random.uniform(-1.0, 1.0, (len(pretrain_vocab), 300))
    unk_id = len(pretrain_vocab)-1

    id2word = dict([(pretrain_vocab[item], item) for item in pretrain_vocab])
    if debug:
        for i in range(10, 13):
            sent_tags = training_data[i][1]
            triggers = [(word_idx, training_data[i][0][word_idx], tag) for word_idx, tag in enumerate(sent_tags) if tag != "none"]
            print "## eg:", training_data[i]
            print triggers
    tags_data = sorted(list(set([tag_text for sent_text, tags_text in training_data for tag_text in tags_text if tag_text != "none"])))
    tags_data = dict(zip(tags_data, range(1, len(tags_data)+1)))
    #tags_data = dict([(tag_text, tag_id+1) for tag_id, tag_text in enumerate(tags_data)])
    tags_data["none"] = 0

    ## text to id
    training_data = [([pretrain_vocab[word_text] if word_text in pretrain_vocab else unk_id for word_text in sent_text_arr], [tags_data.get(tag_text) for tag_text in tags_text_arr]) for sent_text_arr, tags_text_arr in training_data]
    dev_data = [([pretrain_vocab[word_text] if word_text in pretrain_vocab else unk_id for word_text in sent_text_arr], [tags_data.get(tag_text) for tag_text in tags_text_arr]) for sent_text_arr, tags_text_arr in dev_data]
    test_data = [([pretrain_vocab[word_text] if word_text in pretrain_vocab else unk_id for word_text in sent_text_arr], [tags_data.get(tag_text) for tag_text in tags_text_arr]) for sent_text_arr, tags_text_arr in test_data]
    if debug:
        for i in range(10, 13):
            sent_tags = training_data[i][1]
            triggers = [(word_idx, id2word[training_data[i][0][word_idx]], tag) for word_idx, tag in enumerate(sent_tags) if tag != 0]
            print "## eg:", training_data[i]
            print triggers
    return training_data, dev_data, test_data, pretrain_vocab, tags_data, pretrain_embedding, args.model

def load_data(args_arr):
# pretrain embedding: matrix (vocab_size, pretrain_embed_dim)
    pretrain_embedding = loadPretrain(args.pretrain_embed)
    print "## pretrained embedding loaded.", time.asctime(), pretrain_embedding.shape

# vocab: word: word_id
    vocab = loadVocab(args.vocab)
    print "## vocab loaded.", time.asctime()

# train test
    training_data = loadTrainData(args.train)
    print "## train loaded.", args.train, time.asctime()
    #training_data = check_data(training_data, vocab)
    test_data = loadTrainData(args.test)
    print "## test loaded.", args.test, time.asctime()
    #test_data = check_data(test_data, vocab)
    #check_trigger_test(training_data, test_data)

# tags_data: tag_name: tag_id
    tags_data = loadTag(args.tag)
    print "## event tags loaded.", time.asctime()

    #for sent, tag in training_data:
    #    check_trigger(tag)
    #for sent, tag in test_data:
    #    check_trigger(tag)
    return training_data, None, test_data, vocab, tags_data, pretrain_embedding, args.model

def init_embedding(dim1, dim2):
    init_embedding = np.random.uniform(-0.01, 0.01, (dim1, dim2))
    return np.matrix(init_embedding)

def train_func(para_arr, args, data_sets, debug=False):

    vocab_size, tagset_size, embedding_dim = para_arr[:3]
    training_size, dev_size, test_size = para_arr[3:6]
    model_path = para_arr[6]
    gpu = args.gpu

    training_data, dev_data, test_data, vocab, tags_data, pretrain_embedding = data_sets

    random_dim = -1

# init model
    if not args.use_pretrain:
        pretrain_embedding = init_embedding(vocab_size, embedding_dim)

    model_params_to_feed = [vocab_size, tagset_size, embedding_dim, random_dim, pretrain_embedding]

    model = LSTMTrigger(model_params_to_feed, args)

    if args.loss_flag == "cross-entropy":
        loss_function = nn.CrossEntropyLoss()
    else:
        loss_function = nn.NLLLoss()

    parameters = filter(lambda a:a.requires_grad, model.parameters())
    if args.opti_flag == "ada":
        optimizer = optim.Adadelta(parameters, lr=args.lr)
    elif args.opti_flag == "sgd":
        optimizer = optim.SGD(model.parameters(), lr=args.lr)

# training
    valid_train = [(did, item[1]) for did, item in enumerate(training_data) if sum(item[1]) != 0]
    print len(valid_train), valid_train
    best_f1 = -1.0
    for epoch in range(args.epoch_num):
        debug = True
        training_id = 0
        for sent, tags, gold_triggers in training_data:
            if debug and training_id == 9 and model.word_embeddings.weight.grad is not None:
                print "## train word embedding grad:", training_id, torch.sum(model.word_embeddings.weight.grad), model.word_embeddings.weight.grad#[:5, :5]
            if training_id % 100 == 0:
                print "## processed training instance:", training_id, time.asctime()
            iden_tags = [1 if tag != 0 else tag for tag in tags]

            model.zero_grad()
            model.hidden = model.init_hidden(gpu)

            sentence_in = arr2tensor(sent)
            targets = arr2tensor(tags)
            iden_targets = arr2tensor(iden_tags)

            if gpu:
                sentence_in = sentence_in.cuda()
                targets = targets.cuda()
                iden_targets = iden_targets.cuda()

            #if training_id < 1:    debug = True
            #print "## sent(s) for model", sentence_in.size(), sentence_in.size()[0]
            tag_space, tag_scores, tag_space_iden, tag_scores_iden = model(sentence_in, gpu, debug)
            if debug and training_id == 9 and sum(tags) != 0:
                #print "tag space, score", training_id, tag_space.size(), tag_scores.size()
                #print "tag iden space, score", tag_space_iden.size(), tag_scores_iden.size()
                #print "target class, iden", targets.size(), iden_targets.size()
                print "-tag scores", tag_scores.data.size(), tag_scores.data, tag_scores.data.max(1)

            if 1:
                loss = loss_function(tag_scores, targets) + loss_function(tag_scores_iden, iden_targets)
            else:
                loss = loss_function(tag_space, targets)# + loss_function(tag_space_iden, iden_targets)
                #loss = loss_function(tag_space, targets)
                #loss += loss_function(tag_space_iden, iden_targets)
            if debug and training_id == 9:
                print "-loss", loss.data
            loss.backward()
            optimizer.step()
            training_id += 1

        loss_train, prf_train, prf_train_iden = eval_model(training_data, model, loss_function, "train", gpu)
        print "## train results on epoch:", epoch, Tab, loss_train, time.asctime(), Tab,
        outputPRF(prf_train)
        print "## Iden result:", 
        outputPRF(prf_train_iden)

# result on dev
        loss_dev, prf_dev, prf_dev_iden = eval_model(dev_data, model, loss_function, "dev", gpu)
        if prf_dev[2] > best_f1:
            print "##-- New best dev results on epoch", epoch, Tab, best_f1, "(old best)", Tab, loss_dev, time.asctime(), Tab,
            best_f1 = prf_dev[2]
            torch.save(model, model_path)
        else:
            print "##-- dev results on epoch", epoch, Tab, best_f1, "(best f1)", Tab, loss_dev, time.asctime(), Tab,
        outputPRF(prf_dev)
        print "## Iden result:",
        outputPRF(prf_dev_iden)
# result on test
        if epoch >= 10 and epoch % 10 == 0:
            if epoch % 100 == 0:
                model_test = torch.load(model_path)
                loss_test, prf_test, prf_test_iden = eval_model(test_data, model_test, loss_function, "test_final", gpu)
            else:
                loss_test, prf_test, prf_test_iden = eval_model(test_data, model, loss_function, "test", gpu)
            print "##-- test results on epoch", epoch, Tab, loss_test, time.asctime(), Tab,
            outputPRF(prf_test)
            print "## Iden result:",
            outputPRF(prf_test_iden)

# final result on test
    model = torch.load(model_path)
    loss_test, prf_test, prf_test_iden = eval_model(test_data, model, loss_function, "test_final", gpu)
    print "## Final results on test", loss_test, time.asctime(), Tab,
    outputPRF(prf_test)
    print "## Iden result:",
    outputPRF(prf_test_iden)

if __name__ == "__main__":
    args = get_args()

    #######################
    ## default parameters
    embedding_dim = args.embed_dim

    #######################
    ## load datasets
    if len(args.dev) > 0:
        training_data, dev_data, test_data, vocab, tags_data, pretrain_embedding, model_path = load_data2(args)
    else:
        training_data, dev_data, test_data, vocab, tags_data, pretrain_embedding, model_path = load_data(args)
        if args.test_as_dev:
            dev_data = test_data
        else:
            if args.shuffle_train:
                random.shuffle(training_data, lambda: 0.3) # shuffle data before get dev
            training_data = training_data[:-500]
            dev_data = training_data[-500:]
    model_path = model_path + "_" + time.strftime("%Y%m%d%H%M%S", time.gmtime()) + "_"

    vocab_size = len(vocab)
    pretrain_vocab_size, pretrain_embed_dim = pretrain_embedding.shape
    tagset_size = len(tags_data)

    if 0:
        #all_data = test_data
        #all_data = dev_data+test_data
        all_data = training_data+dev_data+test_data
        sent_lens = [len(item[0]) for item in all_data]
        print "## Statistic sent length:", max(sent_lens), min(sent_lens)
        sent_len_counter = Counter(sent_lens)
        for sent_len in range(1, max(sent_lens)+1):
            sent_num = sum([item[1] for item in sent_len_counter.items() if item[0] <= sent_len])
            print sent_len, sent_num, sent_num*100.0/len(sent_lens)
        sys.exit(0)
    if 0:
        output_normal_pretrain(pretrain_embedding, vocab, "../ni_data/f.ace.pretrain300.vectors")
        output_dynet_format(training_data, vocab, tags_data, "../ni_data/f.ace_trigger.train")
        output_dynet_format(dev_data, vocab, tags_data, "../ni_data/f.ace_trigger.dev")
        output_dynet_format(test_data, vocab, tags_data, "../ni_data/f.ace_trigger.test")
        sys.exit(0)


    #######################
    ## store and output all parameters
    if args.use_pretrain: embedding_dim = pretrain_embed_dim

    param_str = "p"+str(embedding_dim) + "_hd" + str(args.hidden_dim) + "_2hd" + str(args.hidden_dim_snd)
    if args.use_conv: param_str += "_f" + str(args.conv_filter_num) + "_c" + str(args.conv_width1) + "_c" + str(args.conv_width2)
    if args.use_pretrain: param_str += "_pf"
    param_str += "_lr" + str(args.lr*100)# + "_" + str() + "_" + str()
    model_path += param_str

    para_arr = [vocab_size, tagset_size, embedding_dim]
    para_arr.extend([len(training_data), len(dev_data), len(test_data), model_path])
    outputParameters(para_arr, args)

    #######################
    # begin to train
    training_data = [(item[0], item[1], get_trigger(item[1])) for item in training_data]
    dev_data = [(item[0], item[1], get_trigger(item[1])) for item in dev_data]
    test_data = [(item[0], item[1], get_trigger(item[1])) for item in test_data]

    if 0: # program debug mode
        training_data = training_data[:2000]
    data_sets = training_data, dev_data, test_data, vocab, tags_data, pretrain_embedding
    train_func(para_arr, args, data_sets)

