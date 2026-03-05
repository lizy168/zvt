# -*- coding: utf-8 -*-

from zvt.broker.qmt import qmt_quote
from zvt.contract.recorder import Recorder
from zvt.domain import Stock


class QMTStockRecorder(Recorder):
    provider = "qmt"
    data_schema = Stock

    def run(self):
        df = qmt_quote.get_entity_list()
        self.logger.info(df.tail())
        self.data_schema.df_to_db(df, provider=self.provider, force_update=True)


if __name__ == "__main__":
    recorder = QMTStockRecorder()
    recorder.run()


# the __all__ is generated
__all__ = ["QMTStockRecorder"]
