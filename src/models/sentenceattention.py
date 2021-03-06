import torch
from torch import nn
from torch.nn.utils.rnn import pad_packed_sequence, PackedSequence

from src.utils import registry


@registry.register("sentence_attention", "SentenceAttention")
class SentenceAttention(torch.nn.Module):
    def __init__(
            self,
            device: str,
            dropout: float,
            word_recurrent_size: int,
            recurrent_size: int,
            attention_dim: int,
    ):
        super().__init__()
        self._device = device
        self.word_recurrent_size = word_recurrent_size
        self.recurrent_size = recurrent_size
        self.dropout = dropout
        self.attention_dim = attention_dim

        assert self.recurrent_size % 2 == 0

        self.encoder = nn.LSTM(
            input_size=self.word_recurrent_size,
            hidden_size=self.recurrent_size // 2,
            dropout=self.dropout,
            bidirectional=True,
            batch_first=True,
        )
        self.dropout = nn.Dropout(dropout)

        # Maps LSTM output to `attention_dim` sized tensor
        self.sentence_weight = nn.Linear(self.recurrent_size, self.attention_dim)

        # Word context vector (u_w) to take dot-product with
        self.sentence_context_weight = nn.Linear(self.attention_dim, 1)

    def recurrent_size(self):
        return self.recurrent_size

    def forward(self, sent_embeddings, doc_perm_idx, doc_valid_bsz, word_att_weights):
        """
        :param sent_embeddings: LongTensor (batch_size * padded_doc_length, sentence recurrent dim)
        :param doc_perm_idx: LongTensor (batch_size)
        :param doc_valid_bsz: LongTensor (max_doc_len)
        :param word_att_weights: LongTensor (batch_size * padded_doc_length, max_sent_len)
        :return: docs embeddings, word attention weights, sentence attention weights
        """

        sent_embeddings = self.dropout(sent_embeddings)

        # Sentence-level LSTM over sentence embeddings
        packed_sentences, _ = self.encoder(PackedSequence(sent_embeddings, doc_valid_bsz))

        u_i = torch.tanh(self.sentence_weight(packed_sentences.data))
        u_w = self.sentence_context_weight(u_i).squeeze(1)
        val = u_w.max()
        att = torch.exp(u_w - val)

        # Restore as sentences by repadding
        att, _ = pad_packed_sequence(PackedSequence(att, doc_valid_bsz), batch_first=True)

        sent_att_weights = att / torch.sum(att, dim=1, keepdim=True)

        # Restore as documents by repadding
        docs, _ = pad_packed_sequence(packed_sentences, batch_first=True)

        # Compute document vectors
        docs = docs * sent_att_weights.unsqueeze(2)
        docs = docs.sum(dim=1)

        # Restore as documents by repadding
        word_att_weights, _ = pad_packed_sequence(PackedSequence(word_att_weights, doc_valid_bsz), batch_first=True)

        # Restore the original order of documents (undo the first sorting)
        _, doc_unperm_idx = doc_perm_idx.sort(dim=0, descending=False)
        docs = docs[doc_unperm_idx]

        word_att_weights = word_att_weights[doc_unperm_idx]
        sent_att_weights = sent_att_weights[doc_unperm_idx]

        return docs, word_att_weights, sent_att_weights
